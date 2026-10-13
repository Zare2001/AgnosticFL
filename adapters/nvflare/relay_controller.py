"""NVFlare server workflow that relays hub rounds to the NVFlare clients.

Runs inside the NVFlare server job (the long-running "flhub-relay" job). It long-polls the
NVFlare adapter service on localhost for work, sends the global model to the requested
clients with the standard ModelController API and posts the client results back.

Besides NVFlare it only needs the stdlib and flhub_core.weights (numpy + safetensors, no torch).
"""

import base64
import hashlib
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import numpy as np

from flhub_core.weights import from_safetensors, to_safetensors
from nvflare.apis.fl_constant import FLMetaKey
from nvflare.app_common.abstract.fl_model import FLModel, ParamsType
from nvflare.app_common.workflows.model_controller import ModelController

# FLModel.meta keys shared with clients/nvflare/client.py
META_TRAIN_CONFIG = "train_config"
META_NUM_EXAMPLES = "num_examples"
META_CODE_VERSION = "client_code_version"


class RelayError(Exception):
    pass


class HubRelayController(ModelController):
    def __init__(
        self,
        adapter_url: str = "http://127.0.0.1:8811",
        poll_wait_s: int = 20,
        cancel_poll_s: float = 2.0,
        task_name: str = "train",
    ):
        """Relay workflow: hub dispatch -> send_model_and_wait -> results back to the adapter.

        Args:
            adapter_url: base URL of the NVFlare adapter service (localhost only).
            poll_wait_s: long-poll duration for GET /relay/next.
            cancel_poll_s: how often a running task checks whether it was cancelled.
            task_name: NVFlare task name sent to the clients.
        """
        # no persistor: the hub owns the global model; client errors must not end the job
        super().__init__(persistor_id="", ignore_result_error=True)
        self.adapter_url = adapter_url.rstrip("/")
        self.poll_wait_s = poll_wait_s
        self.cancel_poll_s = cancel_poll_s
        self.task_name = task_name

    # ---------------------------------------------------------------- HTTP helpers
    def _http(self, method: str, path: str, body=None, timeout: float = 30.0):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.adapter_url + path, data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read() or b"{}")

    @staticmethod
    def _fetch_model(url: str, ref: str) -> dict:
        with urllib.request.urlopen(url, timeout=120) as resp:
            blob = resp.read()
        got = "sha256:" + hashlib.sha256(blob).hexdigest()
        if got != ref:
            raise RelayError(f"model blob hash mismatch: expected {ref}, got {got}")
        return from_safetensors(blob)

    # ---------------------------------------------------------------- workflow
    def _clients(self):
        return sorted(c.name for c in self.engine.get_clients())

    def run(self):
        job_id = self.fl_ctx.get_job_id()
        self.info(f"HubRelayController started (job {job_id}), adapter {self.adapter_url}")
        failures = 0
        while not self.abort_signal.triggered:
            query = urllib.parse.urlencode(
                {"job_id": job_id, "clients": ",".join(self._clients()), "wait": self.poll_wait_s}
            )
            try:
                item = self._http("GET", f"/relay/next?{query}", timeout=self.poll_wait_s + 15)
                failures = 0
            except (urllib.error.URLError, OSError, ValueError) as e:
                failures += 1
                if failures == 1 or failures % 30 == 0:
                    self.warning(f"adapter not reachable ({e}); retrying")
                time.sleep(2.0)
                continue

            kind = item.get("type")
            if kind == "shutdown":
                self.info("shutdown requested by adapter; ending relay job")
                return
            if kind == "task":
                self._run_task(item, job_id)
        self.info("abort signal received; ending relay job")

    def _run_task(self, item: dict, job_id: str):
        task_id = item["task_id"]
        result = {"task_id": task_id, "job_id": job_id, "ok": False, "error": None, "results": []}
        stop_watch = threading.Event()
        try:
            sites = list(item["sites"])
            clients = self._clients()
            missing = [s for s in sites if s not in clients]
            if missing:
                raise RelayError(f"sites not in the NVFlare relay job: {missing} (job clients: {clients})")

            weights = self._fetch_model(item["model_url"], item["model_ref"])
            model = FLModel(
                params_type=ParamsType.FULL,
                params=weights,
                current_round=int(item["round"]),
                meta={META_TRAIN_CONFIG: dict(item.get("train_config") or {})},
            )
            watcher = threading.Thread(target=self._watch_cancel, args=(task_id, stop_watch), daemon=True)
            watcher.start()
            t0 = time.time()
            replies = self.send_model_and_wait(
                task_name=self.task_name,
                data=model,
                targets=sites,
                timeout=int(item.get("deadline_s") or 0),
            )
            self.info(f"task {task_id}: {len(replies)}/{len(sites)} replies in {time.time() - t0:.1f}s")

            got = {}
            for reply in replies:
                site = reply.meta.get("client_name")
                if site in got:
                    raise RelayError(f"duplicate reply from {site}")
                got[site] = self._convert_reply(site, reply)
            lost = [s for s in sites if s not in got]
            if lost:
                raise RelayError(f"no valid result from {lost} (client error, timeout or cancel)")
            result["results"] = [got[s] for s in sites]
            result["ok"] = True
        except Exception as e:  # report every failure to the adapter, keep the relay alive
            result["error"] = f"{type(e).__name__}: {e}"
            self.error(f"task {task_id} failed: {result['error']}")
        finally:
            stop_watch.set()
        self._post_result(result)

    def _convert_reply(self, site: str, reply: FLModel) -> dict:
        if reply.params_type != ParamsType.FULL:
            raise RelayError(f"{site} sent params_type {reply.params_type}, expected FULL")
        params = {k: np.ascontiguousarray(v, dtype=np.float32) for k, v in reply.params.items()}
        meta = reply.meta or {}
        n = meta.get(META_NUM_EXAMPLES, meta.get(FLMetaKey.NUM_STEPS_CURRENT_ROUND))
        if n is None or int(n) <= 0:
            raise RelayError(f"{site} sent no positive '{META_NUM_EXAMPLES}' in FLModel.meta")
        return {
            "site_id": site,
            "weights_b64": base64.b64encode(to_safetensors(params)).decode("ascii"),
            "num_examples": int(n),
            "metrics": {k: float(v) for k, v in (reply.metrics or {}).items()},
            "client_code_version": meta.get(META_CODE_VERSION),
        }

    def _post_result(self, result: dict):
        # the adapter must learn the outcome; retry until it answers or the job is aborted
        for attempt in range(1000):
            try:
                self._http("POST", "/relay/result", result, timeout=120)
                return
            except urllib.error.HTTPError as e:
                if 400 <= e.code < 500:  # e.g. the adapter restarted and forgot the task
                    self.warning(f"adapter rejected result of {result['task_id']}: HTTP {e.code}")
                    return
                if attempt % 30 == 0:
                    self.warning(f"posting result of {result['task_id']} failed ({e}); retrying")
                time.sleep(2.0)
            except (urllib.error.URLError, OSError, ValueError) as e:
                if self.abort_signal.triggered:
                    return
                if attempt % 30 == 0:
                    self.warning(f"posting result of {result['task_id']} failed ({e}); retrying")
                time.sleep(2.0)

    def _watch_cancel(self, task_id: str, stop: threading.Event):
        while not stop.wait(self.cancel_poll_s):
            try:
                state = self._http("GET", f"/relay/tasks/{task_id}", timeout=10).get("state")
            except (urllib.error.URLError, OSError, ValueError):
                continue
            if state == "cancelled":
                self.info(f"task {task_id} cancelled by the hub; cancelling NVFlare tasks")
                self.cancel_all_tasks()
                return

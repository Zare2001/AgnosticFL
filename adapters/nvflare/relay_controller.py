"""NVFlare server workflow that relays hub rounds to the NVFlare clients.

Runs inside the NVFlare server job "flhub-relay" (one long-running job). It long-polls the
NVFlare adapter service with adapters.common.RelayClient, sends the global model to the
requested clients with the standard ModelController API (send_model_and_wait, FULL weights)
and stores each client's returned weights as a hub-core blob for the adapter.
"""

import json
import math
import threading
import time

from nvflare.apis.fl_constant import FLMetaKey
from nvflare.app_common.abstract.fl_model import FLModel, ParamsType
from nvflare.app_common.workflows.model_controller import ModelController

from adapters.common.hubclient import HubError, http_request
from adapters.common.relay import RelayClient

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
        hub_url: str = None,
        poll_timeout_s: float = 20.0,
        cancel_poll_s: float = 2.0,
        task_name: str = "train",
    ):
        """Relay workflow: hub job -> send_model_and_wait -> client weights back to the adapter.

        Args:
            adapter_url: NVFlare adapter service (localhost only).
            hub_url: hub core URL; None = adapters.common default (127.0.0.1:8800 / $FLHUB_HUB_URL).
            poll_timeout_s: long-poll duration for the next hub job.
            cancel_poll_s: how often a running round checks whether the hub cancelled it.
            task_name: NVFlare task name sent to the clients.
        """
        # no persistor: the hub owns the global model; client errors must not end the job
        super().__init__(persistor_id="", ignore_result_error=True)
        self.adapter_url = adapter_url
        self.hub_url = hub_url
        self.poll_timeout_s = poll_timeout_s
        self.cancel_poll_s = cancel_poll_s
        self.task_name = task_name

    def _job_clients(self) -> dict:
        """{client name: online|offline} for the clients deployed in this job."""
        names = sorted(c.name for c in self.engine.get_clients())
        return {n: "offline" if self.get_client_disconnect_time(n) else "online" for n in names}

    def run(self):
        relay = RelayClient(self.adapter_url, self.hub_url)
        self.info(f"relay started in job {self.fl_ctx.get_job_id()}; adapter {self.adapter_url}")
        unreachable_since = None
        while not self.abort_signal.triggered:
            try:
                relay.report_sites(self._job_clients())
            except HubError:
                pass  # next_job() below waits and reports the problem
            job = relay.next_job(timeout=self.poll_timeout_s)
            if job.get("error"):
                if unreachable_since is None:
                    unreachable_since = time.monotonic()
                    self.warning(f"adapter not reachable: {job['error']}; retrying")
                continue
            if unreachable_since is not None:
                self.info(f"adapter reachable again after {time.monotonic() - unreachable_since:.0f}s")
                unreachable_since = None
            if job["type"] == "shutdown":
                self.info("shutdown requested by the adapter; ending the relay job")
                return
            if job["type"] == "train":
                self._train(relay, job)
        self.info("abort signal received; ending the relay job")

    def _train(self, relay: RelayClient, job: dict):
        job_id, sites = job["job_id"], list(job["sites"])
        stop_watch = threading.Event()
        try:
            clients = self._job_clients()
            absent = [s for s in sites if clients.get(s) != "online"]
            if absent:
                raise RelayError(f"sites not online in the NVFlare relay job: {absent} (job clients: {clients})")
            model = FLModel(
                params_type=ParamsType.FULL,
                params=relay.get_model(job),
                current_round=int(job["round"]),
                meta={META_TRAIN_CONFIG: dict(job["config"])},
            )
            threading.Thread(target=self._watch_cancel, args=(job_id, stop_watch), daemon=True).start()
            t0 = time.monotonic()
            replies = self.send_model_and_wait(
                task_name=self.task_name,
                data=model,
                targets=sites,
                timeout=max(1, math.ceil(float(job.get("deadline_s") or 0))),
            )
            self.info(f"job {job_id}: {len(replies)}/{len(sites)} replies in {time.monotonic() - t0:.1f}s")
            results = {}
            for reply in replies:
                site = reply.meta.get("client_name")
                if site in results or site not in sites:
                    raise RelayError(f"unexpected or duplicate reply from {site!r}")
                results[site] = self._to_result(relay, site, reply)
            lost = [s for s in sites if s not in results]
            if lost:
                raise RelayError(f"no valid result from {lost} (client error, deadline or cancel)")
            relay.post_result(job_id, [results[s] for s in sites])
        except Exception as e:  # report every failure to the hub; keep the relay job alive
            msg = f"NVFlare relay: {type(e).__name__}: {e}"
            self.error(f"job {job_id} failed: {msg}")
            try:
                relay.post_error(job_id, msg)
            except HubError as post_exc:  # e.g. 404: the hub already cancelled the task
                self.warning(f"could not report the failure of {job_id}: {post_exc}")
        finally:
            stop_watch.set()

    @staticmethod
    def _to_result(relay: RelayClient, site: str, reply: FLModel) -> dict:
        if reply.params_type != ParamsType.FULL or not reply.params:
            raise RelayError(f"{site} sent params_type {reply.params_type}; FULL weights are required")
        meta = reply.meta or {}
        n = meta.get(META_NUM_EXAMPLES, meta.get(FLMetaKey.NUM_STEPS_CURRENT_ROUND))
        if n is None or int(n) <= 0:
            raise RelayError(f"{site} sent no positive '{META_NUM_EXAMPLES}' in FLModel.meta")
        return relay.result(site, reply.params, int(n), reply.metrics, meta.get(META_CODE_VERSION, "unknown"))

    def _watch_cancel(self, job_id: str, stop: threading.Event):
        """Best-effort cancel: once the adapter no longer waits for this job, stop the NVFlare task."""
        while not stop.wait(self.cancel_poll_s):
            try:
                status, body = http_request(f"{self.adapter_url.rstrip('/')}/relay/status", timeout=10)
                pending = json.loads(body).get("pending", []) if status == 200 else None
            except (HubError, ValueError):
                continue
            if pending is not None and job_id not in pending:
                self.info(f"job {job_id} is no longer pending at the hub (cancelled or deadline); cancelling it")
                self.cancel_all_tasks()
                return

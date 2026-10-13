"""vantage6 adapter: flhub/0.1 on top of a vantage6 4.15 server (PROTOCOL.md §5).

    python -m adapters.vantage6 [--port 8812] [--sites v6-h3 v6-h4] [--image REGISTRY/flhub/v6-train:TAG]

The adapter is an ordinary vantage6 *user* (`hub-adapter` of organisation `hub-operator`).
A round becomes one vantage6 task (method `train` of the algorithm image) for the sites'
organisations, with the global weights inline as base64 safetensors. The task state comes
from the vantage6 run states; the algorithm results become hub-core blobs and envelopes.
Site id = organisation name. Password from $V6_ADAPTER_PASSWORD (never on the command line).
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import threading
import time

from adapters.common.base import BaseAdapter, FrameworkError, SiteResult
from adapters.common.hubclient import HubClient

log = logging.getLogger("flhub.adapter.vantage6")

# vantage6.common.task_status.TaskStatus values that end a run unsuccessfully
FAILED = {"failed", "start failed", "non-existing Docker image", "crashed", "killed by user", "not allowed",
          "unknown error"}
POLL_S = 1.0
PROGRESS_EVERY = 5  # polls between run listings (runs carry the task input, so not every poll)


class Vantage6Adapter(BaseAdapter):
    adapter_name = "vantage6"
    framework = "vantage6"
    framework_version = "unknown"

    def __init__(self, sites: list[str], *, server_url: str, server_port: int, api_path: str, username: str,
                 password: str, collaboration: str, image: str, hub: HubClient | None = None) -> None:
        super().__init__(sites, hub)
        self.server = (server_url.rstrip("/"), int(server_port), api_path)
        self.username, self._password = username, password
        self.collaboration, self.image = collaboration, image
        self._lock = threading.RLock()
        self._client = None
        self.collab_id: int | None = None
        self.org_ids: dict[str, int] = {}  # site id -> organisation id
        self.v6_tasks: dict[str, int] = {}  # flhub task id -> vantage6 task id

    # ---- vantage6 session -----------------------------------------------------------------
    def _login(self):
        from vantage6.client import UserClient

        url, port, path = self.server
        root = logging.getLogger()
        handlers, level = list(root.handlers), root.level
        try:
            client = UserClient(url, port, path, log_level="warn")
        finally:  # UserClient takes over the root logger; give it its own quiet logger instead
            root.handlers[:] = handlers
            root.setLevel(level)
        client.log = logging.getLogger("vantage6.client")
        client.log.setLevel(logging.WARNING)
        client.authenticate(self.username, self._password)
        client.setup_encryption(None)  # the collaboration is not encrypted
        self.framework_version = client.util.get_server_version()["version"]
        cols = [c for c in client.collaboration.list(per_page=100)["data"] if c["name"] == self.collaboration]
        if not cols:
            raise FrameworkError(f"vantage6 collaboration {self.collaboration!r} not found")
        orgs = {o["name"]: o["id"] for o in client.organization.list(per_page=100)["data"]}
        missing = [s for s in self.sites if s not in orgs]
        if missing:
            raise FrameworkError(f"no vantage6 organisation named {missing}")
        self.collab_id = cols[0]["id"]
        self.org_ids = {s: orgs[s] for s in self.sites}
        return client

    def _call(self, fn, retry: bool = True):
        """fn(client) under the session lock; log in again once if the session broke."""
        with self._lock:
            for attempt in (0, 1):
                try:
                    if self._client is None:
                        self._client = self._login()
                    return fn(self._client)
                except FrameworkError:
                    raise
                except Exception as exc:
                    self._client = None
                    if attempt or not retry:
                        raise FrameworkError(f"vantage6: {type(exc).__name__}: {exc}") from exc
                    log.warning("vantage6 call failed (%s); logging in again", exc)

    def _kill(self, v6_id: int) -> None:
        try:
            self._call(lambda c: c.task.kill(v6_id), retry=False)
        except Exception as exc:
            log.warning("could not kill vantage6 task %s: %s", v6_id, exc)

    # ---- BaseAdapter hooks -----------------------------------------------------------------
    def site_status(self) -> dict[str, str]:
        nodes = self._call(lambda c: c.node.list(collaboration=self.collab_id, per_page=100)["data"])
        by_org = {n["organization"]["id"]: n.get("status") for n in nodes}
        return {s: "online" if by_org.get(o) == "online" else "offline" for s, o in self.org_ids.items()}

    def on_cancel(self, task) -> None:
        if task.task_id in self.v6_tasks:
            self._kill(self.v6_tasks[task.task_id])

    def run_round(self, task, weights, client_config):
        model = self.hub.get_blob(task.model_ref)  # exact bytes of the round model
        self._call(lambda c: None)  # make sure the session and organisation ids exist
        sites = {str(self.org_ids[s]): s for s in task.sites}
        input_ = {"method": "train", "kwargs": {"weights_b64": base64.b64encode(model).decode("ascii"),
                                                "train_config": client_config, "sites": sites}}
        created = self._call(lambda c: c.task.create(
            collaboration=self.collab_id, organizations=[int(o) for o in sites], name=f"flhub-r{task.round}",
            image=self.image, description=f"{task.task_id} (hub round {task.round})", input_=input_,
            databases=[{"label": "default"}]), retry=False)
        if not isinstance(created, dict) or "id" not in created:
            raise FrameworkError(f"vantage6 task creation failed: {created}")
        v6_id = self.v6_tasks[task.task_id] = created["id"]
        log.info("%s -> vantage6 task %s on %s (%s)", task.task_id, v6_id, task.sites, self.image)

        polls = 0
        while True:
            if task.cancelled:  # cancel/deadline: the base marks the task terminal right away
                for _ in range(100):
                    if task.terminal:
                        break
                    time.sleep(0.05)
                log.info("%s cancelled, vantage6 task %s killed", task.task_id, v6_id)
                return []
            status = self._call(lambda c: c.request(f"task/{v6_id}/status"))["status"]
            polls += 1
            if status in FAILED:
                runs = self._call(lambda c: c.run.from_task(v6_id)["data"])
                self._kill(v6_id)
                raise FrameworkError(f"vantage6 task {v6_id} {status}: " + "; ".join(
                    f"{self._site_of(r)} {r['status']}: {(r.get('log') or '').strip()[-600:]}"
                    for r in runs if r["status"] in FAILED))
            if status == "completed":
                break
            if polls % PROGRESS_EVERY == 0:
                runs = self._call(lambda c: c.run.from_task(v6_id)["data"])
                task.done = sum(r["status"] == "completed" for r in runs)
            time.sleep(POLL_S)
        return self._results(task, v6_id)

    # ---- results ---------------------------------------------------------------------------
    def _site_of(self, run: dict) -> str:
        org = (run.get("organization") or {}).get("id")
        return next((s for s, o in self.org_ids.items() if o == org), f"org{org}")

    def _results(self, task, v6_id: int) -> list[SiteResult]:
        runs = self._call(lambda c: c.run.from_task(v6_id)["data"])
        items = self._call(lambda c: c.result.from_task(v6_id)["data"])
        site_of_run = {r["id"]: self._site_of(r) for r in runs}
        results = []
        for item in items:
            site = site_of_run.get((item.get("run") or {}).get("id", item.get("id")))
            if site not in task.sites:
                raise FrameworkError(f"vantage6 task {v6_id}: result from unexpected site {site}")
            if not item.get("result"):
                raise FrameworkError(f"{site}: vantage6 run returned no result")
            out = json.loads(item["result"])
            if out.get("site_id") != site:
                raise FrameworkError(f"{site}: algorithm reports site {out.get('site_id')!r}")
            if out.get("base_model") != task.model_ref:
                raise FrameworkError(f"{site}: trained from {out.get('base_model')}, not {task.model_ref}")
            ref = self.hub.put_blob(base64.b64decode(out["weights_b64"]))
            results.append(SiteResult(site, int(out["num_examples"]), out.get("metrics") or {},
                                      out.get("client_code_version", "unknown"), weights_ref=ref))
        task.done = len(results)
        return results


def main(argv: list[str] | None = None) -> None:
    env = os.environ.get
    ap = argparse.ArgumentParser(prog="python -m adapters.vantage6")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=int(env("ADAPTER_PORT", "8812")))
    ap.add_argument("--sites", nargs="+", default=env("V6_SITES", "v6-h3 v6-h4").split())
    ap.add_argument("--server-url", default=env("V6_SERVER_URL", "http://127.0.0.1"))
    ap.add_argument("--server-port", type=int, default=int(env("V6_SERVER_PORT", "7601")))
    ap.add_argument("--api-path", default=env("V6_API_PATH", "/api"))
    ap.add_argument("--collaboration", default=env("V6_COLLABORATION", "flhub"))
    ap.add_argument("--username", default=env("V6_ADAPTER_USER", "hub-adapter"))
    ap.add_argument("--image", default=env("V6_ALGO_IMAGE"), help="algorithm image the nodes pull")
    ap.add_argument("--hub", default=env("FLHUB_HUB_URL"))
    args = ap.parse_args(argv)
    if not args.image:
        ap.error("--image (or V6_ALGO_IMAGE) is required")
    if not env("V6_ADAPTER_PASSWORD"):
        ap.error("V6_ADAPTER_PASSWORD is not set")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    adapter = Vantage6Adapter(args.sites, server_url=args.server_url, server_port=args.server_port,
                              api_path=args.api_path, username=args.username, password=env("V6_ADAPTER_PASSWORD"),
                              collaboration=args.collaboration, image=args.image, hub=HubClient(args.hub))
    try:
        status = adapter.site_status()
        log.info("vantage6 %s, sites %s", adapter.framework_version, status)
    except FrameworkError as exc:  # server not up yet: discover reports "unknown" until it is
        log.warning("vantage6 server not reachable yet: %s", exc)
    adapter.serve(args.host, args.port)

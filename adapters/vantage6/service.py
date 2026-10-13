"""vantage6 adapter: flhub/0.2 on top of a vantage6 4.15 server (PROTOCOL.md §5, §7).

    python -m adapters.vantage6 [--port 8812] [--sites v6-h3 v6-h4] [--image REGISTRY/flhub/v6-train:TAG]

The adapter is an ordinary vantage6 *user* (`hub-adapter` of organisation `hub-operator`).
A round becomes one vantage6 task per site (method `train` of the algorithm image, for that
site's organisation) with the global weights inline as base64 safetensors. Per-site tasks map
flhub/0.2 one to one onto vantage6: the cheap `task/<id>/status` gives each site's state (a run
listing would carry every run's input), a site's result is fetched and reported as soon as its
task completes, `task.kill` cancels exactly that site (pending runs included), and a failed site
leaves the other sites' tasks untouched. The node picks the data by database label: the label of
the dispatch's `data_variant` (`db_label`), else `default` (docs/DECISIONS.md).
Site id = organisation name. Password from $V6_ADAPTER_PASSWORD (never on the command line).
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import re
import threading
import time

from adapters.common.base import BaseAdapter, FrameworkError, SiteResult
from adapters.common.hubclient import HubClient

log = logging.getLogger("flhub.adapter.vantage6")

# vantage6.common.task_status.TaskStatus values that end a run unsuccessfully
FAILED = {"failed", "start failed", "non-existing Docker image", "crashed", "killed by user", "not allowed",
          "unknown error"}
POLL_S = 1.0
# delete a round's vantage6 tasks (input and results live in the server DB) once the round is
# settled if the model is at least this large: keeps the server DB at one round for E3/E4/E7
PURGE_MIN_MB = float(os.environ.get("V6_PURGE_MIN_MB", "16"))
CTL_ATTEMPTS = 2  # control calls give up after this many connection errors (the client retries forever)
_ROOT_LOGGER_LOCK = threading.Lock()  # UserClient() replaces the root handlers; sessions log in in parallel


def db_label(data_variant: str | None) -> str:
    """vantage6 database label of a data variant: node database labels must be Python identifiers."""
    if not data_variant:
        return "default"
    label = re.sub(r"\W", "_", data_variant, flags=re.ASCII)
    return label if label.isidentifier() else "v_" + label


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
        self._meta = threading.RLock()
        self._sessions: dict[str, object] = {}
        self._session_locks: dict[str, threading.RLock] = {}
        self.collab_id: int | None = None
        self.org_ids: dict[str, int] = {}  # site id -> organisation id (registered organisations)
        self.v6_tasks: dict[str, dict[str, int]] = {}  # flhub task id -> {site: vantage6 task id}

    # ---- vantage6 sessions ---------------------------------------------------------------
    def _login(self):
        from vantage6.client import UserClient

        url, port, path = self.server
        root = logging.getLogger()
        with _ROOT_LOGGER_LOCK:
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
        if missing:  # e.g. E5 extra sites before they are registered: offline, the others still work
            log.warning("no vantage6 organisation named %s; reported offline", missing)
        self.collab_id = cols[0]["id"]
        self.org_ids = {s: orgs[s] for s in self.sites if s in orgs}
        return client

    def _call(self, fn, retry: bool = True, session: str = "ctl"):
        """fn(client) on one vantage6 session: `ctl` for states and kills, `data-<site>` for a site's
        input and result, so a long transfer never blocks the polls. Log in again once if it broke."""
        with self._meta:
            lock = self._session_locks.setdefault(session, threading.RLock())
        with lock:
            for attempt in (0, 1):
                try:
                    client = self._sessions.get(session)
                    if client is None:
                        client = self._sessions[session] = self._login()
                    return fn(client)
                except FrameworkError:
                    raise
                except Exception as exc:
                    self._sessions.pop(session, None)
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
        def nodes(c):
            if len(self.org_ids) < len(self.sites):  # sites registered after login (E5 add_sites.sh)
                orgs = {o["name"]: o["id"] for o in c.organization.list(per_page=100)["data"]}
                self.org_ids = {s: orgs[s] for s in self.sites if s in orgs}
            res = c.request("node", params={"collaboration_id": self.collab_id, "per_page": 100},
                            attempts_on_timeout=CTL_ATTEMPTS)
            if "data" not in res:
                raise FrameworkError(f"vantage6 server not reachable: {res.get('msg')}")
            return res["data"]

        nodes = self._call(nodes)
        by_org = {n["organization"]["id"]: n.get("status") for n in nodes}
        return {s: "online" if s in self.org_ids and by_org.get(self.org_ids[s]) == "online" else "offline"
                for s in self.sites}

    def on_cancel(self, task) -> None:
        for v6_id in list(self.v6_tasks.get(task.task_id, {}).values()):
            self._kill(v6_id)

    def on_cancel_sites(self, task, sites: list[str]) -> None:
        ids = self.v6_tasks.get(task.task_id, {})
        for site in sites:
            if site in ids:
                self._kill(ids[site])

    def run_round(self, task, weights, client_config):
        model = self.hub.get_blob(task.model_ref)  # exact bytes of the round model
        self._call(lambda c: None)  # make sure the session and organisation ids exist
        b64 = base64.b64encode(model).decode("ascii")
        ids = self.v6_tasks.setdefault(task.task_id, {})
        workers = [threading.Thread(target=self._create, args=(task, site, b64, client_config, ids), daemon=True)
                   for site in task.active_sites()]
        for w in workers:
            w.start()
        for w in workers:
            w.join()
        del b64
        try:
            self._follow(task, ids)
        finally:
            if len(model) >= PURGE_MIN_MB * 2**20:
                self._delete(task, ids)
        return []  # every site was reported on its own

    # ---- per-site vantage6 tasks -----------------------------------------------------------
    def _create(self, task, site: str, b64: str, client_config: dict, ids: dict[str, int]) -> None:
        org = self.org_ids.get(site)
        if org is None:
            self.report_site_error(task, site, f"no vantage6 organisation named {site!r}")
            return
        if task.cancelled or task.site_state.get(site) not in ("queued", "running"):
            return  # cancelled before its vantage6 task existed
        label = db_label(task.data_variant)
        input_ = {"method": "train", "kwargs": {"weights_b64": b64, "train_config": client_config,
                                                "sites": {str(org): site}}}
        try:
            created = self._call(lambda c: c.task.create(
                collaboration=self.collab_id, organizations=[org], name=f"flhub-r{task.round}", image=self.image,
                description=f"{task.task_id} {site} (hub round {task.round})", input_=input_,
                databases=[{"label": label}]), retry=False, session=f"data-{site}")
        except FrameworkError as exc:
            self.report_site_error(task, site, f"vantage6 task creation failed: {exc}")
            return
        if not isinstance(created, dict) or "id" not in created:
            self.report_site_error(task, site, f"vantage6 task creation failed: {created}")
            return
        with self._meta:
            ids[site] = created["id"]
        log.info("%s %s -> vantage6 task %s (%s, database %s)", task.task_id, site, created["id"], self.image, label)
        if task.cancelled or task.site_cancelled(site):  # cancelled while its task was created
            self._kill(created["id"])

    def _follow(self, task, ids: dict[str, int]) -> None:
        """Poll every active site's vantage6 task; report each result or failure as it happens.
        A vantage6 server outage (E6e restart) only delays this: the deadline still applies."""
        fetching: dict[str, threading.Thread] = {}
        outage = None
        while not task.cancelled:
            active = [s for s in task.active_sites() if s in ids]
            if not active:
                return
            for site in active:
                if site in fetching:
                    continue
                try:
                    status = self._call(lambda c, v=ids[site]: c.request(
                        f"task/{v}/status", attempts_on_timeout=CTL_ATTEMPTS)).get("status")
                except FrameworkError:
                    status = None
                if status is None:  # connection error, or a reply without a status
                    if outage is None:
                        outage = time.monotonic()
                        log.warning("%s: vantage6 server not reachable; waiting", task.task_id)
                    break
                if outage is not None:
                    log.info("%s: vantage6 server reachable again after %.0fs", task.task_id,
                             time.monotonic() - outage)
                    outage = None
                if status == "completed":
                    fetching[site] = threading.Thread(target=self._collect_site, args=(task, site, ids[site]),
                                                      daemon=True)
                    fetching[site].start()
                elif status in FAILED:
                    self._fail_site(task, site, ids[site], status)
            for site in [s for s, t in fetching.items() if not t.is_alive()]:
                del fetching[site]
            time.sleep(POLL_S)

    def _collect_site(self, task, site: str, v6_id: int) -> None:
        try:
            items = self._call(lambda c: c.result.from_task(v6_id)["data"], session=f"data-{site}")
        except FrameworkError as exc:  # transient: the next poll tries again
            log.warning("%s %s: result of vantage6 task %s not fetched (%s); retrying", task.task_id, site,
                        v6_id, exc)
            return
        try:
            self.report_site(task, self._site_result(task, site, v6_id, items))
        except FrameworkError as exc:
            self.report_site_error(task, site, str(exc))
        except Exception as exc:
            self.report_site_error(task, site, f"{type(exc).__name__}: {exc}")

    def _site_result(self, task, site: str, v6_id: int, items: list[dict]) -> SiteResult:
        if len(items) != 1 or not items[0].get("result"):
            raise FrameworkError(f"{site}: vantage6 task {v6_id} returned {len(items)} results, expected one")
        out = json.loads(items[0]["result"])
        if out.get("site_id") != site:
            raise FrameworkError(f"{site}: algorithm reports site {out.get('site_id')!r}")
        if out.get("base_model") != task.model_ref:
            raise FrameworkError(f"{site}: trained from {out.get('base_model')}, not {task.model_ref}")
        ref = self.hub.put_blob(base64.b64decode(out["weights_b64"]))
        return SiteResult(site, int(out["num_examples"]), out.get("metrics") or {},
                          out.get("client_code_version", "unknown"), weights_ref=ref)

    def _fail_site(self, task, site: str, v6_id: int, status: str) -> None:
        try:  # the raw run listing (not run.from_task): no decoding of the input it carries
            runs = self._call(lambda c: c.request("run", params={"task_id": v6_id}), session=f"data-{site}")["data"]
            logs = "; ".join(f"{r['status']}: {(r.get('log') or '').strip()[-600:]}" for r in runs)
        except Exception as exc:
            logs = f"(run log not available: {exc})"
        self.report_site_error(task, site, f"vantage6 task {v6_id} {status}: {logs}")

    def _delete(self, task, ids: dict[str, int]) -> None:
        for site, v6_id in list(ids.items()):
            try:
                self._call(lambda c, v=v6_id: c.task.delete(v), retry=False)
            except Exception as exc:
                log.warning("could not delete vantage6 task %s (%s): %s", v6_id, site, exc)
        log.info("%s: deleted vantage6 tasks %s (model >= %s MB)", task.task_id, sorted(ids.values()), PURGE_MIN_MB)


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

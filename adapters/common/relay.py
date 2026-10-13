"""Relay pattern for frameworks whose server workflow must run inside the framework server
(NVFlare ModelController, Flower ServerApp). That process, the relay, long-polls the adapter
service for jobs and posts results back. Weights travel as hub-core blob refs.

Adapter side:  class FlowerAdapter(RelayAdapter): ...   (optionally override site_status())
Relay side, inside the framework server:

    relay = RelayClient("http://127.0.0.1:8813")
    while True:
        job = relay.next_job(timeout=30)          # {"type": "train" | "idle" | "shutdown", ...}
        if job["type"] == "shutdown":
            break
        if job["type"] != "train":
            continue
        weights = relay.get_model(job)            # ordered {name: float32 ndarray}
        ...send weights + job["config"] to job["sites"], wait for the replies...
        relay.post_result(job["job_id"], [relay.result(site, w, n, metrics, code_version), ...])
        # on failure: relay.post_error(job["job_id"], "<framework error text>")

Routes added to the adapter port:
    GET  /relay/next?timeout=30   train job | {"type":"idle"} | {"type":"shutdown"} (delivered once)
         train job = {"type","job_id","round","sites","model":{"ref","url"},"config","deadline_s"}
         config = dispatch train_config + "round"; pass it unchanged to flhub_core.train
    POST /relay/result     {"job_id","results":[{"site_id","weights_ref","num_examples","metrics",
                            "client_code_version"}]}  or  {"job_id","error":"..."}
    POST /relay/progress   {"job_id","done":n}
    POST /relay/sites      {"sites":{"<site_id>":"online"|"offline"}}  the relay's view, for discover
    POST /relay/shutdown   ask the relay to exit at its next poll
    GET  /relay/status     liveness of the relay as seen by the adapter
"""

from __future__ import annotations

import json
import queue
import threading
import time
from collections.abc import Mapping

import numpy as np
from flhub_core.weights import as_float32

from .base import BaseAdapter, FrameworkError, SiteResult
from .envelope import finite_metrics
from .hubclient import HubClient, HubError, http_request
from .protocol import is_ref
from .server import Request, RpcServer
from .tasks import Task


class _Pending:
    def __init__(self) -> None:
        self.event = threading.Event()
        self.results: list[dict] | None = None
        self.error: str | None = None


class RelayAdapter(BaseAdapter):
    site_status_ttl_s = 90.0

    def __init__(self, sites: list[str], hub: HubClient | None = None) -> None:
        super().__init__(sites, hub)
        self._queue: queue.Queue[dict] = queue.Queue()
        self._pending: dict[str, _Pending] = {}
        self._lock = threading.Lock()
        self._shutdown = threading.Event()
        self._reported: dict[str, str] = {}
        self._reported_at = 0.0
        self._last_poll = 0.0

    # ---- BaseAdapter hooks ---------------------------------------------------------------
    def site_status(self) -> dict[str, str]:
        if self._reported and time.monotonic() - self._reported_at <= self.site_status_ttl_s:
            return {s: self._reported.get(s, "offline") for s in self.sites}
        return {}

    def run_round(self, task: Task, weights: dict[str, np.ndarray], client_config: dict) -> list[SiteResult]:
        pending = _Pending()
        with self._lock:
            self._pending[task.task_id] = pending
        self._queue.put({"type": "train", "job_id": task.task_id, "round": task.round, "sites": list(task.sites),
                         "model": {"ref": task.model_ref, "url": self.hub.blob_url(task.model_ref)},
                         "config": client_config})
        try:
            while not pending.event.wait(0.5):
                if task.cancelled or task.terminal:
                    raise FrameworkError("task ended before the relay replied")
        finally:
            with self._lock:
                self._pending.pop(task.task_id, None)
        if pending.error:
            raise FrameworkError(pending.error)
        return [SiteResult(site_id=r["site_id"], num_examples=int(r["num_examples"]),
                           metrics=dict(r.get("metrics") or {}),
                           client_code_version=str(r.get("client_code_version", "unknown")),
                           weights_ref=r["weights_ref"]) for r in pending.results or []]

    def relay_seen_s(self) -> float | None:
        """Seconds since the relay last polled, or None if it never did."""
        return None if not self._last_poll else time.monotonic() - self._last_poll

    def request_relay_shutdown(self) -> None:
        self._shutdown.set()

    # ---- HTTP routes ---------------------------------------------------------------------
    def extra_routes(self, server: RpcServer) -> None:
        server.route("GET", "/relay/next", self._route_next)
        server.route("POST", "/relay/result", self._route_result)
        server.route("POST", "/relay/progress", self._route_progress)
        server.route("POST", "/relay/sites", self._route_sites)
        server.route("POST", "/relay/shutdown", lambda req: (self.request_relay_shutdown(), (200, {"ok": True}))[1])
        server.route("GET", "/relay/status", self._route_status)

    def _route_next(self, req: Request):
        timeout = min(max(float(req.query.get("timeout", 30)), 0.0), 120.0)
        end = time.monotonic() + timeout
        while True:
            self._last_poll = time.monotonic()
            if self._shutdown.is_set():
                self._shutdown.clear()
                return 200, {"type": "shutdown"}
            remaining = end - time.monotonic()
            if remaining <= 0:
                return 200, {"type": "idle"}
            try:
                job = self._queue.get(timeout=min(remaining, 1.0))
            except queue.Empty:
                continue
            with self._lock:
                live = job["job_id"] in self._pending
            if live:  # skip jobs whose task was cancelled or timed out while queued
                task = self.tasks.get(job["job_id"])
                return 200, {**job, "deadline_s": max(1.0, task.seconds_left())}

    def _route_result(self, req: Request):
        body = req.json() or {}
        with self._lock:
            pending = self._pending.get(body.get("job_id"))
        if pending is None:
            return 404, {"error": f"no pending job {body.get('job_id')!r}"}
        if body.get("error"):
            pending.error = str(body["error"])
        else:
            results = body.get("results")
            bad = not isinstance(results, list) or not all(
                isinstance(r, dict) and isinstance(r.get("site_id"), str) and is_ref(r.get("weights_ref"))
                and isinstance(r.get("num_examples"), (int, float)) for r in results)
            if bad:
                pending.error = f"relay sent malformed results: {json.dumps(results)[:300]}"
            else:
                pending.results = results
        pending.event.set()
        return 200, {"ok": True}

    def _route_progress(self, req: Request):
        body = req.json() or {}
        try:
            task = self.tasks.get(body.get("job_id"))
        except Exception:
            return 404, {"error": "unknown job"}
        task.done = min(int(body.get("done", 0)), task.total)
        return 200, {"ok": True}

    def _route_sites(self, req: Request):
        sites = (req.json() or {}).get("sites")
        if not isinstance(sites, dict):
            return 400, {"error": "expected {'sites': {site_id: status}}"}
        self._reported = {str(k): (v if v in ("online", "offline", "unknown") else "unknown") for k, v in sites.items()}
        self._reported_at = time.monotonic()
        return 200, {"ok": True}

    def _route_status(self, req: Request):
        seen = self.relay_seen_s()
        return 200, {"relay_last_poll_s": None if seen is None else round(seen, 1), "queued": self._queue.qsize(),
                     "pending": sorted(self._pending), "sites": self.site_status()}


class RelayClient:
    """Used by the relay process inside the framework server."""

    def __init__(self, adapter_url: str, hub_url: str | None = None) -> None:
        self.base = adapter_url.rstrip("/")
        self.hub = HubClient(hub_url)

    def _post(self, path: str, payload: dict) -> None:
        status, body = http_request(self.base + path, "POST", json.dumps(payload).encode(), "application/json")
        if status != 200:
            raise HubError(f"POST {path}: HTTP {status} {body[:200]!r}")

    def next_job(self, timeout: float = 30) -> dict:
        """Next instruction; on connection problems waits briefly and returns idle."""
        try:
            status, body = http_request(f"{self.base}/relay/next?timeout={timeout}", "GET", timeout=timeout + 30)
        except HubError as exc:
            time.sleep(2)
            return {"type": "idle", "error": str(exc)}
        if status != 200:
            time.sleep(2)
            return {"type": "idle", "error": f"HTTP {status}"}
        return json.loads(body)

    def get_model(self, job: Mapping) -> dict[str, np.ndarray]:
        return self.hub.get_weights(job["model"]["ref"])

    def result(self, site_id: str, weights: Mapping, num_examples: int, metrics: Mapping | None = None,
               client_code_version: str = "unknown") -> dict:
        """Store a site's weights in the hub core and return its result entry."""
        return {"site_id": site_id, "weights_ref": self.hub.put_weights(as_float32(weights)),
                "num_examples": int(num_examples), "metrics": finite_metrics(metrics or {}),
                "client_code_version": str(client_code_version)}

    def post_result(self, job_id: str, results: list[dict]) -> None:
        self._post("/relay/result", {"job_id": job_id, "results": results})

    def post_error(self, job_id: str, message: str) -> None:
        self._post("/relay/result", {"job_id": job_id, "error": message})

    def post_progress(self, job_id: str, done: int) -> None:
        self._post("/relay/progress", {"job_id": job_id, "done": int(done)})

    def report_sites(self, sites: Mapping[str, str]) -> None:
        self._post("/relay/sites", {"sites": dict(sites)})

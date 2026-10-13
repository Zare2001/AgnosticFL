"""BaseAdapter: the flhub/0.1 methods, framework-free. A framework adapter subclasses it and
implements two hooks:

    site_status() -> {site_id: "online" | "offline" | "unknown"}
    run_round(task, weights, client_config) -> [SiteResult, ...]   (blocking, worker thread)

The base validates params, fetches the global model from the hub core, runs run_round()
in a worker thread with deadline and cancel handling, stores result weights as hub-core
blobs and builds the envelopes. run_round() should poll `task.cancelled` and
`task.deadline_exceeded()` while it waits, and may update `task.done` for progress.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, field

import numpy as np
from flhub_core.weights import as_float32

from .envelope import Envelope, finite_metrics
from .hubclient import HubClient
from .protocol import (
    FRAMEWORK_ERROR,
    INVALID_PARAMS,
    PROTOCOL,
    TASK_NOT_COMPLETED,
    TRAIN_CONFIG_KEYS,
    UNKNOWN_SITE,
    RpcError,
    check_protocol,
    is_ref,
    train_config_hash,
)
from .server import RpcServer
from .tasks import Task, TaskRegistry

log = logging.getLogger("flhub.adapter")


@dataclass
class SiteResult:
    site_id: str
    num_examples: int
    metrics: dict[str, float] = field(default_factory=dict)
    client_code_version: str = "unknown"
    weights: Mapping[str, np.ndarray] | None = None  # either the weights ...
    weights_ref: str | None = None  # ... or a hub-core blob ref that already holds them


class FrameworkError(RuntimeError):
    """Raise from run_round() with the framework's own error text."""


class BaseAdapter:
    adapter_name = "base"
    framework = "base"
    framework_version = "0"
    capabilities = {"payload": "full_weights", "dtypes": ["float32"], "max_payload_mb": 64,
                    "evaluate": False, "min_round_interval_s": 0}
    default_deadline_s = 1800.0

    def __init__(self, sites: list[str], hub: HubClient | None = None) -> None:
        self.sites = list(sites)
        self.hub = hub or HubClient()
        self.tasks = TaskRegistry()

    # ---- hooks for framework adapters -------------------------------------------------
    def site_status(self) -> dict[str, str]:
        raise NotImplementedError

    def run_round(self, task: Task, weights: dict[str, np.ndarray], client_config: dict) -> list[SiteResult]:
        raise NotImplementedError

    def on_cancel(self, task: Task) -> None:
        """Optional best-effort cancel inside the framework."""

    def extra_routes(self, server: RpcServer) -> None:
        """Optional extra HTTP routes on the adapter port (e.g. /relay/*)."""

    # ---- flhub/0.1 methods --------------------------------------------------------------
    def rpc_discover(self, params: dict) -> dict:
        check_protocol(params)
        try:
            status = self.site_status()
        except Exception as exc:
            log.warning("site_status failed: %s", exc)
            status = {}
        return {
            "adapter": self.adapter_name,
            "framework": self.framework,
            "framework_version": self.framework_version,
            "protocol": PROTOCOL,
            "sites": [{"site_id": s, "status": status.get(s, "unknown")} for s in self.sites],
            "capabilities": dict(self.capabilities),
        }

    def rpc_dispatch(self, params: dict) -> dict:
        check_protocol(params)
        rnd = params.get("round")
        model = params.get("model") or {}
        cfg = params.get("train_config")
        sites = params.get("sites") or list(self.sites)
        deadline = params.get("deadline_s", self.default_deadline_s)
        if not isinstance(rnd, int) or isinstance(rnd, bool) or rnd < 0:
            raise RpcError(INVALID_PARAMS, "round must be an int >= 0")
        if not isinstance(model, dict) or not is_ref(model.get("ref")):
            raise RpcError(INVALID_PARAMS, "model.ref must be 'sha256:<64 hex>'")
        if not isinstance(cfg, dict) or any(k not in cfg for k in TRAIN_CONFIG_KEYS):
            raise RpcError(INVALID_PARAMS, f"train_config needs {list(TRAIN_CONFIG_KEYS)}")
        if not isinstance(sites, list) or not sites or len(set(sites)) != len(sites):
            raise RpcError(INVALID_PARAMS, "sites must be a non-empty list without duplicates")
        unknown = [s for s in sites if s not in self.sites]
        if unknown:
            raise RpcError(UNKNOWN_SITE, f"unknown sites {unknown}; this adapter serves {self.sites}")
        if not isinstance(deadline, (int, float)) or deadline <= 0:
            raise RpcError(INVALID_PARAMS, "deadline_s must be > 0")
        task = self.tasks.create(self.adapter_name, rnd, sites, model["ref"], cfg, deadline)
        threading.Thread(target=self._work, args=(task,), name=task.task_id, daemon=True).start()
        threading.Thread(target=self._watchdog, args=(task,), name=f"{task.task_id}-wd", daemon=True).start()
        return {"task_id": task.task_id}

    def rpc_tasks_get(self, params: dict) -> dict:
        check_protocol(params)
        return self.tasks.get(params.get("task_id")).status()

    def rpc_collect(self, params: dict) -> dict:
        check_protocol(params)
        task = self.tasks.get(params.get("task_id"))
        if task.state != "completed":
            raise RpcError(TASK_NOT_COMPLETED, f"task {task.task_id} is {task.state}", {"error": task.error})
        return {"task_id": task.task_id, "envelopes": list(task.envelopes)}

    def rpc_cancel(self, params: dict) -> dict:
        check_protocol(params)
        task = self.tasks.get(params.get("task_id"))
        if not task.terminal:
            task.cancel_event.set()
            try:
                self.on_cancel(task)
            except Exception as exc:
                log.warning("on_cancel(%s) failed: %s", task.task_id, exc)
            self.tasks.finish(task, "cancelled", "cancelled by orchestrator")
        return {"task_id": task.task_id, "state": task.state}

    # ---- internals -----------------------------------------------------------------------
    def _work(self, task: Task) -> None:
        if not self.tasks.start(task):
            return
        try:
            weights = self.hub.get_weights(task.model_ref)
            results = self.run_round(task, weights, task.client_config)
            if task.terminal:  # cancelled or timed out meanwhile
                return
            envelopes = self._envelopes(task, results)
        except Exception as exc:
            log.error("task %s failed: %s", task.task_id, exc, exc_info=not isinstance(exc, FrameworkError))
            self.tasks.finish(task, "failed", f"{type(exc).__name__}: {exc}")
            return
        if self.tasks.finish(task, "completed", envelopes=envelopes):
            log.info("task %s completed: %s", task.task_id, [e["site_id"] for e in envelopes])

    def _watchdog(self, task: Task) -> None:
        while not task.terminal:
            if task.deadline_exceeded():
                task.cancel_event.set()
                if self.tasks.finish(task, "failed", f"deadline of {task.deadline_s:.0f}s exceeded "
                                                     f"({task.done}/{task.total} sites done)"):
                    try:
                        self.on_cancel(task)
                    except Exception as exc:
                        log.warning("on_cancel(%s) failed: %s", task.task_id, exc)
                return
            time.sleep(0.5)

    def _envelopes(self, task: Task, results: list[SiteResult]) -> list[dict]:
        by_site = {}
        for r in results:
            if r.site_id in by_site:
                raise FrameworkError(f"two results for site {r.site_id}")
            by_site[r.site_id] = r
        missing = [s for s in task.sites if s not in by_site]
        extra = [s for s in by_site if s not in task.sites]
        if missing or extra:
            raise FrameworkError(f"results do not match the task sites: missing {missing}, unexpected {extra}")
        tc_hash = train_config_hash(task.train_config)
        out = []
        for site in task.sites:
            r = by_site[site]
            if r.weights_ref is not None:
                if not self.hub.has_blob(r.weights_ref):
                    raise FrameworkError(f"{site}: weights blob {r.weights_ref} not in the hub core")
                ref = r.weights_ref
            elif r.weights is not None:
                ref = self.hub.put_weights(as_float32(r.weights))
            else:
                raise FrameworkError(f"{site}: result has no weights")
            out.append(Envelope(round=task.round, site_id=site, framework=self.framework, base_model=task.model_ref,
                                weights=ref, num_examples=int(r.num_examples), metrics=finite_metrics(r.metrics),
                                train_config_hash=tc_hash,
                                client_code_version=str(r.client_code_version)).to_dict())
        return out

    # ---- serving -------------------------------------------------------------------------
    def build_server(self, host: str = "127.0.0.1", port: int = 0) -> RpcServer:
        srv = RpcServer(host, port, name=f"{self.adapter_name}-adapter")
        srv.method("discover", self.rpc_discover)
        srv.method("dispatch", self.rpc_dispatch)
        srv.method("tasks/get", self.rpc_tasks_get)
        srv.method("collect", self.rpc_collect)
        srv.method("cancel", self.rpc_cancel)
        self.extra_routes(srv)
        return srv

    def serve(self, host: str = "127.0.0.1", port: int = 0) -> None:
        self.build_server(host, port).serve_forever()


def framework_error(message: str) -> RpcError:
    return RpcError(FRAMEWORK_ERROR, message)

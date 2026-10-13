"""BaseAdapter: the flhub/0.2 methods (PROTOCOL.md), framework-free. A framework adapter
subclasses it and implements two hooks:

    site_status() -> {site_id: "online" | "offline" | "unknown"}
    run_round(task, weights, client_config) -> [SiteResult, ...]   (blocking, worker thread)

The base validates params, fetches the global model from the hub core, runs run_round()
in a worker thread with deadline and cancel handling, stores result weights as hub-core
blobs and builds the envelopes. run_round() should poll `task.cancelled` and
`task.deadline_exceeded()` while it waits, and may update `task.done` for progress.

flhub/0.2: run_round() should call `self.report_site(task, SiteResult)` as soon as one site
is done (partial collect and per-site progress depend on it) and `self.report_site_error(task,
site, text)` when one site fails; it may then return [] or the full list (sites reported
before are skipped). It should stop waiting for a site once `task.site_cancelled(site)`;
on_cancel_sites() is called right after a per-site cancel. Results of cancelled sites are
dropped and logged.
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
    SUPPORTED_PROTOCOLS,
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
    capabilities = {"payload": "full_weights", "dtypes": ["float32"], "max_payload_mb": 1024,
                    "evaluate": False, "min_round_interval_s": 0,
                    "site_progress": True, "partial_collect": True, "site_cancel": True}
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
        """Optional best-effort cancel inside the framework (whole task)."""

    def on_cancel_sites(self, task: Task, sites: list[str]) -> None:
        """Optional: stop the framework work of these sites (per-site cancel, task goes on)."""

    def extra_routes(self, server: RpcServer) -> None:
        """Optional extra HTTP routes on the adapter port (e.g. /relay/*)."""

    # ---- per-site results (flhub/0.2) ----------------------------------------------------
    def report_site(self, task: Task, result: SiteResult) -> bool:
        """Store one site's result as an envelope now; False if the site no longer wants it."""
        if result.site_id not in task.sites:
            raise FrameworkError(f"result from {result.site_id!r}, which is not a site of {task.task_id}")
        state = task.site_state.get(result.site_id)
        if state == "done":
            return False
        if state not in ("queued", "running"):
            log.info("%s: dropping late result of %s (site %s)", task.task_id, result.site_id, state)
            return False
        envelope = self._envelope(task, result)
        if not self.tasks.site_done(task, result.site_id, envelope):
            log.info("%s: dropping late result of %s (site %s)", task.task_id, result.site_id,
                     task.site_state.get(result.site_id))
            return False
        return True

    def report_site_error(self, task: Task, site: str, error: str) -> bool:
        """One site failed (the others may go on); False if the site was no longer active."""
        if site not in task.sites:
            raise FrameworkError(f"error from {site!r}, which is not a site of {task.task_id}")
        ok = self.tasks.site_failed(task, site, error)
        if ok:
            log.warning("%s: site %s failed: %s", task.task_id, site, error)
        return ok

    # ---- flhub/0.2 methods (a superset of flhub/0.1) ----------------------------------------
    def rpc_discover(self, params: dict) -> dict:
        version = check_protocol(params)
        try:
            status = self.site_status()
        except Exception as exc:
            log.warning("site_status failed: %s", exc)
            status = {}
        return {
            "adapter": self.adapter_name,
            "framework": self.framework,
            "framework_version": self.framework_version,
            "protocol": version,
            "protocols": list(SUPPORTED_PROTOCOLS),
            "sites": [{"site_id": s, "status": status.get(s, "unknown")} for s in self.sites],
            "capabilities": dict(self.capabilities),
        }

    def rpc_dispatch(self, params: dict) -> dict:
        version = check_protocol(params)
        rnd = params.get("round")
        model = params.get("model") or {}
        cfg = params.get("train_config")
        sites = params.get("sites") or list(self.sites)
        deadline = params.get("deadline_s", self.default_deadline_s)
        variant = params.get("data_variant")  # flhub/0.2 optional hint; train_config stays opaque
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
        if variant is not None and (not isinstance(variant, str) or not variant):
            raise RpcError(INVALID_PARAMS, "data_variant must be a non-empty string")
        task = self.tasks.create(self.adapter_name, rnd, sites, model["ref"], cfg, deadline, protocol=version,
                                 data_variant=variant)
        threading.Thread(target=self._work, args=(task,), name=task.task_id, daemon=True).start()
        threading.Thread(target=self._watchdog, args=(task,), name=f"{task.task_id}-wd", daemon=True).start()
        return {"task_id": task.task_id}

    def rpc_tasks_get(self, params: dict) -> dict:
        check_protocol(params)
        task = self.tasks.get(params.get("task_id"))
        with self.tasks.lock:  # a consistent snapshot of the task and site states
            return task.status()

    def rpc_collect(self, params: dict) -> dict:
        check_protocol(params)
        task = self.tasks.get(params.get("task_id"))
        partial = params.get("partial", False)
        if not isinstance(partial, bool):
            raise RpcError(INVALID_PARAMS, "partial must be true or false")
        with self.tasks.lock:
            if partial:  # flhub/0.2: the sites done so far, in any task state
                return {"task_id": task.task_id, "partial": True, "state": task.state,
                        "sites": dict(task.site_state), "envelopes": task.envelopes}
            if task.state != "completed":
                raise RpcError(TASK_NOT_COMPLETED, f"task {task.task_id} is {task.state}", {"error": task.error})
            return {"task_id": task.task_id, "envelopes": task.envelopes}

    def rpc_cancel(self, params: dict) -> dict:
        check_protocol(params)
        task = self.tasks.get(params.get("task_id"))
        sites = params.get("sites")
        if sites is not None:  # flhub/0.2 per-site cancel: the other sites go on
            if (not isinstance(sites, list) or not sites or len(set(sites)) != len(sites)
                    or any(s not in task.sites for s in sites)):
                raise RpcError(INVALID_PARAMS, f"sites must be a non-empty subset of the task's sites {task.sites}")
            hit = self.tasks.cancel_sites(task, sites)
            if hit:
                log.info("%s: cancelled sites %s (task now %s)", task.task_id, hit, task.state)
                hook = self.on_cancel if task.terminal else lambda t: self.on_cancel_sites(t, hit)
                try:
                    hook(task)
                except Exception as exc:
                    log.warning("cancel hook for %s failed: %s", task.task_id, exc)
        elif not task.terminal:
            task.cancel_event.set()
            try:
                self.on_cancel(task)
            except Exception as exc:
                log.warning("on_cancel(%s) failed: %s", task.task_id, exc)
            self.tasks.finish(task, "cancelled", "cancelled by orchestrator")
        with self.tasks.lock:
            return {"task_id": task.task_id, "state": task.state, "sites": dict(task.site_state)}

    # ---- internals -----------------------------------------------------------------------
    def _work(self, task: Task) -> None:
        if not self.tasks.start(task):
            return
        try:
            weights = self.hub.get_weights(task.model_ref)
            results = self.run_round(task, weights, task.client_config) or []
            if task.terminal:  # cancelled, timed out or settled per site meanwhile
                self._drop_late(task, results)
                return
            self._check_results(task, results)
            for r in results:
                self.report_site(task, r)
        except Exception as exc:
            log.error("task %s failed: %s", task.task_id, exc, exc_info=not isinstance(exc, FrameworkError))
            self.tasks.finish(task, "failed", f"{type(exc).__name__}: {exc}")
            return
        if self.tasks.finish(task, "completed") or task.state == "completed":
            log.info("task %s %s: %s", task.task_id, task.state, task.site_state)

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

    @staticmethod
    def _check_results(task: Task, results: list[SiteResult]) -> None:
        seen, extra = set(), []
        for r in results:
            if r.site_id in seen:
                raise FrameworkError(f"two results for site {r.site_id}")
            seen.add(r.site_id)
            if r.site_id not in task.sites:
                extra.append(r.site_id)
        if extra:
            raise FrameworkError(f"results for sites that are not in the task: {extra}")

    @staticmethod
    def _drop_late(task: Task, results: list[SiteResult]) -> None:
        late = [r.site_id for r in results if task.site_state.get(r.site_id) != "done"]
        if late:
            log.info("%s is %s: dropping late results of %s", task.task_id, task.state, late)

    def _envelope(self, task: Task, r: SiteResult) -> dict:
        site = r.site_id
        if r.weights_ref is not None:
            if not self.hub.has_blob(r.weights_ref):
                raise FrameworkError(f"{site}: weights blob {r.weights_ref} not in the hub core")
            ref = r.weights_ref
        elif r.weights is not None:
            ref = self.hub.put_weights(as_float32(r.weights))
        else:
            raise FrameworkError(f"{site}: result has no weights")
        return Envelope(round=task.round, site_id=site, framework=self.framework, base_model=task.model_ref,
                        weights=ref, num_examples=int(r.num_examples), metrics=finite_metrics(r.metrics),
                        train_config_hash=train_config_hash(task.train_config),
                        client_code_version=str(r.client_code_version), protocol=task.protocol).to_dict()

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

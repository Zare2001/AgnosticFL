"""Round executors: who trains the round's global model at the sites.

AdapterExecutor  flhub/0.2 (0.1 fallback) to the framework adapters, i.e. the real network.
                 One task per adapter; per-site progress is polled every poll_interval_s.
                 Strict (min_sites null): every participant must deliver, a failed site fails the
                 round at once (the runner retries). min_sites m: after deadline_s, or as soon as
                 no participant is running anymore, the round goes on with the >= m sites that
                 are done: partial collect, per-site cancel of the rest (dropped and recorded).
DirectExecutor   flhub_core.train in this process or in worker processes (hub.sim).
Both return a RoundOutcome; aggregation, evaluation and logging are shared (run.py).
"""

from __future__ import annotations

import contextlib
import logging
import multiprocessing
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field

import numpy as np
from flhub_core.train import train

import flhub_core
from adapters.common import AdapterClient, Envelope, HubClient, RpcError, envelope_problems, weights_problems
from adapters.common.protocol import PROTOCOL, TERMINAL_STATES, client_config, train_config_hash

from .config import RunConfig

log = logging.getLogger("flhub.orchestrator")


@dataclass
class SiteUpdate:
    site_id: str
    framework: str
    weights: dict[str, np.ndarray]
    num_examples: int
    metrics: dict[str, float]
    client_code_version: str
    envelope: dict | None = field(default=None, repr=False)
    adapter: str | None = None
    t_result: float | None = None  # seconds after the round started when the site was seen done


@dataclass
class RoundOutcome:
    updates: list[SiteUpdate]
    participants: list[str]
    dropped: dict[str, str] = field(default_factory=dict)  # site -> reason
    adapters: dict[str, dict] = field(default_factory=dict)  # name -> task_id, t_dispatch, fanout
    duration: float = 0.0


class RoundFailed(RuntimeError):
    pass


def _v1_site_state(task_state: str) -> str:
    return {"completed": "done", "failed": "failed", "cancelled": "cancelled"}.get(task_state, "running")


class AdapterExecutor:
    def __init__(self, cfg: RunConfig, hub: HubClient, model_spec: list[dict],
                 data_fp: dict[str, str] | None = None) -> None:
        self.cfg = cfg
        self.hub = hub
        self.train_config = dict(cfg.train_config)
        self.tc_hash = train_config_hash(self.train_config)
        self.model_spec = model_spec
        self.names = [s["name"] for s in model_spec]
        self.clients = {a.name: AdapterClient(a.url) for a in cfg.adapters}
        self.frameworks: dict[str, str] = {}
        self.data_fp = {s: int(fp[:8], 16) for s, fp in (data_fp or {}).items()}

    # ---- readiness ------------------------------------------------------------------------------
    def _discover(self, sites: list[str]) -> tuple[dict, list[str]]:
        info: dict = {"executor": "adapters", "frameworks": {}, "sites": {}, "protocols": {}}
        pending = []
        for a in self.cfg.adapters:
            wanted = [s for s in a.sites if s in sites]
            if not wanted:
                continue
            client = self.clients[a.name]
            try:
                d = client.negotiate()
            except RpcError as exc:
                pending.append(f"{a.name}: {exc.message}")
                continue
            self.frameworks[a.name] = d["framework"]
            info["frameworks"][d["framework"]] = d["framework_version"]
            info["protocols"][a.name] = client.protocol
            status = {s["site_id"]: s["status"] for s in d["sites"]}
            for site in wanted:
                info["sites"][site] = d["framework"]
                if status.get(site) != "online":
                    pending.append(f"{a.name}/{site}: {status.get(site, 'not served')}")
        return info, pending

    def prepare(self, sites: list[str] | None = None, online_wait_s: float | None = None) -> dict:
        """discover the adapters of these sites and wait until the sites (only those) are online."""
        sites = sites or self.cfg.sites
        timeout = self.cfg.online_wait_s if online_wait_s is None else online_wait_s
        end = time.time() + timeout
        while True:
            info, pending = self._discover(sites)
            if not pending:
                return info
            if time.time() > end:
                raise RoundFailed(f"sites not online after {timeout:.0f}s: {pending}")
            log.info("waiting for sites: %s", ", ".join(pending))
            time.sleep(5)

    def wait_online(self, sites: list[str], timeout: float) -> float:
        t0 = time.time()
        self.prepare(sites, timeout)
        return time.time() - t0

    # ---- one round --------------------------------------------------------------------------------
    def run_round(self, rnd: int, weights, model_ref: str, participants: list[str],
                  min_sites: int | None = None) -> RoundOutcome:
        cfg = self.cfg
        t0 = time.monotonic()
        need = len(participants) if min_sites is None else min(int(min_sites), len(participants))
        adapter_deadline = cfg.deadline_s + (60.0 if min_sites is not None else 0.0)  # the hub cuts first
        tasks: dict[str, dict] = {}
        for a in cfg.adapters:
            sites = [s for s in a.sites if s in participants]
            if not sites:
                continue
            try:
                tid = self.clients[a.name].dispatch(rnd, {"ref": model_ref, "url": self.hub.blob_url(model_ref)},
                                                    self.train_config, sites, adapter_deadline,
                                                    data_variant=cfg.data_variant)
            except RpcError as exc:
                self._cancel_all(tasks)
                raise RoundFailed(f"round {rnd}: dispatch to {a.name} failed: {exc.code} {exc.message}") from exc
            tasks[a.name] = {"task_id": tid, "sites": sites, "t_dispatch": time.monotonic() - t0, "final": False}
        done_at: dict[str, float] = {}
        failed: dict[str, str] = {}
        deadline = t0 + cfg.deadline_s
        while True:
            self._poll(tasks, t0, done_at, failed)
            active = [s for s in participants if s not in done_at and s not in failed]
            if not active or (min_sites is None and failed) or time.monotonic() >= deadline:
                break
            if len(done_at) + len(active) < need:
                break  # the minimum can no longer be reached
            time.sleep(cfg.poll_interval_s)
        active = [s for s in participants if s not in done_at and s not in failed]
        if min_sites is None and (failed or active):
            self._cancel_all(tasks)
            raise RoundFailed(f"round {rnd}: failed {failed}; still running at the deadline {active}")
        if len(done_at) < need:
            self._cancel_all(tasks)
            raise RoundFailed(f"round {rnd}: {len(done_at)} of {len(participants)} sites done, need {need}; "
                              f"failed {failed}; late {active}")
        envelopes: dict[str, tuple[str, dict]] = {}
        for name, t in tasks.items():
            late = [s for s in t["sites"] if s in active]
            if late:
                self._cancel_sites(name, t, late)
            for env in self._collect(name, t):
                envelopes[env.get("site_id")] = (name, env)
        updates = []
        for site in participants:
            if site not in envelopes:
                continue
            name, env = envelopes[site]
            u = self._validate(name, rnd, model_ref, env, participants)
            u.t_result = done_at.get(site, time.monotonic() - t0)
            updates.append(u)
        if len(updates) < need:
            raise RoundFailed(f"round {rnd}: collected {len(updates)} envelopes, need {need}")
        got = {u.site_id for u in updates}
        dropped = {s: failed.get(s, "late: cancelled at the deadline") for s in participants if s not in got}
        timing = {}
        for name, t in tasks.items():
            ts = [u.t_result for u in updates if u.adapter == name]
            timing[name] = {"task_id": t["task_id"], "t_dispatch": t["t_dispatch"],
                            "fanout": (max(ts) - t["t_dispatch"]) if ts else None}
        return RoundOutcome(updates, list(participants), dropped, timing, time.monotonic() - t0)

    def _poll(self, tasks: dict, t0: float, done_at: dict, failed: dict) -> None:
        for name, t in tasks.items():
            if t["final"]:
                continue
            try:
                st = self.clients[name].get_task(t["task_id"])
            except RpcError as exc:  # adapter restarted or unreachable: its open sites are lost
                for s in t["sites"]:
                    if s not in done_at:
                        failed.setdefault(s, f"{name}: {exc.message}")
                t["final"] = True
                continue
            states = st.get("sites") or {s: _v1_site_state(st["state"]) for s in t["sites"]}
            now = time.monotonic() - t0
            for s in t["sites"]:
                state = states.get(s)
                if state == "done":
                    done_at.setdefault(s, now)
                elif state in ("failed", "cancelled") and s not in done_at:
                    failed.setdefault(s, (st.get("site_errors") or {}).get(s) or st.get("error") or state)
            t["state"] = st["state"]
            if st["state"] in TERMINAL_STATES:
                t["final"] = True

    def _v2(self, name: str) -> bool:
        return self.clients[name].protocol == PROTOCOL

    def _collect(self, name: str, t: dict) -> list[dict]:
        """flhub/0.2: partial collect (any state); 0.1: full collect, or nothing if the task did not complete."""
        client = self.clients[name]
        try:
            if self._v2(name):
                return client.collect_partial(t["task_id"])["envelopes"]
            if t.get("state") != "completed":
                return []
            return client.collect(t["task_id"])
        except RpcError as exc:
            raise RoundFailed(f"collect from {name} failed: {exc.code} {exc.message}") from exc

    def _cancel_sites(self, name: str, t: dict, sites: list[str]) -> None:
        with contextlib.suppress(RpcError):
            if self._v2(name):
                self.clients[name].cancel(t["task_id"], sites=sites)
            else:
                self.clients[name].cancel(t["task_id"])

    def _cancel_all(self, tasks: dict) -> None:
        for name, t in tasks.items():
            with contextlib.suppress(RpcError):
                self.clients[name].cancel(t["task_id"])

    def _validate(self, name: str, rnd: int, model_ref: str, env_d: dict, participants: list[str]) -> SiteUpdate:
        env = Envelope.from_dict(env_d)
        problems = envelope_problems(env, round=rnd, base_model=model_ref, expected_sites=participants,
                                     train_config_hash=self.tc_hash, protocol=self.clients[name].protocol)
        if env.framework != self.frameworks.get(name, env.framework):
            problems.append(f"framework {env.framework} != {self.frameworks[name]}")
        want_fp = self.data_fp.get(env.site_id)
        if want_fp is not None and env.metrics.get("data_fp") != want_fp:
            problems.append(f"data_fp {env.metrics.get('data_fp')} != manifest {want_fp} (wrong data variant?)")
        if self.cfg.strict_code_version and env.client_code_version != flhub_core.code_version():
            problems.append(f"client code {env.client_code_version} != hub {flhub_core.code_version()}")
        weights = None
        if not problems:
            try:
                weights = self.hub.get_weights(env.weights, self.names)
            except Exception as exc:
                problems.append(f"weights blob {env.weights}: {exc}")
            else:
                problems += weights_problems(weights, self.model_spec)
        if problems:
            raise RoundFailed(f"envelope from {name}/{env.site_id} rejected: {problems}")
        if env.client_code_version != flhub_core.code_version():
            log.warning("%s runs flhub_core %s, hub has %s", env.site_id, env.client_code_version,
                        flhub_core.code_version())
        return SiteUpdate(env.site_id, env.framework, weights, env.num_examples, dict(env.metrics),
                          env.client_code_version, env_d, name)


# ---- in-process / worker-process training (hub.sim) -------------------------------------------------
# DataSpec: ("variant", "<dataset>-<pid>")  in-memory partitions from the npz (hub side)
#           ("stage1", data_dir)            the stage-1 <site>.csv files
#           ("pooled", dataset)             the whole train split as one pseudo-site
_DATA: dict = {}


def load_site_data(spec: tuple, site: str):
    key = (spec, site)
    if key not in _DATA:
        kind, arg = spec
        if kind == "variant":
            from flhub_core.data import variant_site_data

            _DATA.update({(spec, s): d for s, d in variant_site_data(arg).items()})
        elif kind == "stage1":
            from flhub_core.data import load_site, materialize, site_path

            path = site_path(site, arg)
            _DATA[key] = load_site(path if path.exists() else materialize(site, arg)).resolve(None)
        elif kind == "pooled":
            from flhub_core.data import load_train_pooled

            _DATA[key] = load_train_pooled(dataset=arg)
        else:
            raise ValueError(f"unknown data spec {spec}")
    return _DATA[key]


def _worker_init() -> None:
    import torch

    torch.set_num_threads(1)


def _train_site(job: tuple) -> tuple:
    spec, site, weights, cfg = job
    w, n, m = train(weights, load_site_data(spec, site), cfg)
    return site, w, n, m


class DirectExecutor:
    """Every participant trains with flhub_core.train (the code the hospitals run)."""

    def __init__(self, spec: tuple, train_config: dict, framework: str = "sim", workers: int = 0) -> None:
        self.spec = spec
        self.train_config = dict(train_config)
        self.framework = framework
        self.workers = int(workers)
        self._pool = None

    def prepare(self, sites=None, online_wait_s=None) -> dict:
        return {"executor": "direct", "frameworks": {self.framework: flhub_core.__version__},
                "sites": {s: self.framework for s in sites or []}, "workers": self.workers, "data": list(self.spec)}

    def wait_online(self, sites, timeout) -> float:
        return 0.0

    def pool(self) -> ProcessPoolExecutor:
        if self._pool is None:
            self._pool = ProcessPoolExecutor(self.workers, mp_context=multiprocessing.get_context("spawn"),
                                             initializer=_worker_init)
        return self._pool

    def run_round(self, rnd: int, weights, model_ref: str, participants: list[str],
                  min_sites: int | None = None) -> RoundOutcome:
        t0 = time.monotonic()
        cfg = client_config(self.train_config, rnd)
        jobs = [(self.spec, s, weights, cfg) for s in participants]
        if self.workers > 1 and len(jobs) > 1:
            results = list(self.pool().map(_train_site, jobs))
        else:
            results = [_train_site(j) for j in jobs]
        updates = [SiteUpdate(site, self.framework, w, n, m, flhub_core.code_version(), adapter=self.framework,
                              t_result=time.monotonic() - t0) for site, w, n, m in results]
        return RoundOutcome(updates, list(participants), {}, {}, time.monotonic() - t0)

    def close(self) -> None:
        if self._pool is not None:
            self._pool.shutdown()
            self._pool = None

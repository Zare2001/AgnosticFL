"""Round executors: who trains the round's global model at the sites.

AdapterExecutor  talks flhub/0.1 to the framework adapters (the real network).
DirectExecutor   calls flhub_core.train in-process (hub/sim reference and centralized baseline).
Both return SiteUpdates; FedAvg, evaluation and logging are shared (hub/orchestrator/run.py).
"""

from __future__ import annotations

import contextlib
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import numpy as np
from flhub_core.data import SiteData
from flhub_core.train import train

import flhub_core
from adapters.common import AdapterClient, Envelope, HubClient, RpcError, envelope_problems, weights_problems
from adapters.common.protocol import client_config, train_config_hash

from .config import AdapterSpec

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


class RoundFailed(RuntimeError):
    pass


class DirectExecutor:
    """Every site trains in this process with flhub_core.train (same code the hospitals run)."""

    def __init__(self, site_data: dict[str, SiteData], train_config: dict, framework: str = "sim") -> None:
        self.site_data = site_data
        self.train_config = dict(train_config)
        self.framework = framework

    def prepare(self, online_wait_s: float = 0) -> dict:
        return {"executor": "direct", "frameworks": {self.framework: flhub_core.__version__},
                "sites": {s: self.framework for s in self.site_data}}

    def run_round(self, rnd: int, weights: dict[str, np.ndarray], model_ref: str) -> list[SiteUpdate]:
        updates = []
        for site, data in self.site_data.items():
            w, n, m = train(weights, data, client_config(self.train_config, rnd))
            updates.append(SiteUpdate(site, self.framework, w, n, m, flhub_core.code_version()))
        return updates


class AdapterExecutor:
    """Dispatches each round to all adapters in parallel and validates what comes back."""

    def __init__(self, adapters: list[AdapterSpec], hub: HubClient, train_config: dict, model_spec: list[dict],
                 deadline_s: float = 1800.0, poll_s: float = 1.0) -> None:
        self.adapters = adapters
        self.hub = hub
        self.train_config = dict(train_config)
        self.tc_hash = train_config_hash(train_config)
        self.model_spec = model_spec
        self.names = [s["name"] for s in model_spec]
        self.deadline_s = deadline_s
        self.poll_s = poll_s
        self.clients = {a.name: AdapterClient(a.url) for a in adapters}
        self.frameworks: dict[str, str] = {}

    def prepare(self, online_wait_s: float = 600.0) -> dict:
        """discover every adapter and wait until all configured sites are online."""
        end = time.time() + online_wait_s
        info: dict = {"executor": "adapters", "frameworks": {}, "sites": {}}
        while True:
            pending = []
            for a in self.adapters:
                try:
                    d = self.clients[a.name].discover()
                except RpcError as exc:
                    pending.append(f"{a.name}: {exc.message}")
                    continue
                self.frameworks[a.name] = d["framework"]
                info["frameworks"][d["framework"]] = d["framework_version"]
                status = {s["site_id"]: s["status"] for s in d["sites"]}
                for site in a.sites:
                    info["sites"][site] = d["framework"]
                    if status.get(site) != "online":
                        pending.append(f"{a.name}/{site}: {status.get(site, 'not served')}")
            if not pending:
                return info
            if time.time() > end:
                raise RoundFailed(f"sites not online after {online_wait_s:.0f}s: {pending}")
            log.info("waiting for sites: %s", ", ".join(pending))
            time.sleep(5)

    def run_round(self, rnd: int, weights: dict[str, np.ndarray], model_ref: str) -> list[SiteUpdate]:
        with ThreadPoolExecutor(max_workers=len(self.adapters)) as pool:
            futures = {a.name: pool.submit(self._one_adapter, a, rnd, model_ref) for a in self.adapters}
            errors, updates = [], []
            for name, fut in futures.items():
                try:
                    updates.extend(fut.result())
                except Exception as exc:
                    errors.append(f"{name}: {exc}")
        if errors:
            raise RoundFailed(f"round {rnd} failed: " + "; ".join(errors))
        return updates

    def _one_adapter(self, a: AdapterSpec, rnd: int, model_ref: str) -> list[SiteUpdate]:
        client = self.clients[a.name]
        task_id = client.dispatch(rnd, {"ref": model_ref, "url": self.hub.blob_url(model_ref)},
                                  self.train_config, a.sites, self.deadline_s)
        end = time.time() + self.deadline_s + 60
        while True:
            st = client.get_task(task_id)
            if st["state"] == "completed":
                break
            if st["state"] in ("failed", "cancelled"):
                raise RoundFailed(f"task {task_id} {st['state']}: {st.get('error')}")
            if time.time() > end:
                with contextlib.suppress(Exception):
                    client.cancel(task_id)
                raise RoundFailed(f"task {task_id} still {st['state']} after the deadline")
            time.sleep(self.poll_s)
        return [self._validate(a, rnd, model_ref, e) for e in self._check_sites(a, client.collect(task_id))]

    def _check_sites(self, a: AdapterSpec, envelopes: list[dict]) -> list[dict]:
        got = [e.get("site_id") for e in envelopes]
        if sorted(got) != sorted(a.sites):
            raise RoundFailed(f"{a.name} returned envelopes for {got}, expected {a.sites}")
        return envelopes

    def _validate(self, a: AdapterSpec, rnd: int, model_ref: str, env_d: dict) -> SiteUpdate:
        env = Envelope.from_dict(env_d)
        problems = envelope_problems(env, round=rnd, base_model=model_ref, expected_sites=a.sites,
                                     train_config_hash=self.tc_hash)
        if env.framework != self.frameworks.get(a.name, env.framework):
            problems.append(f"framework {env.framework} != {self.frameworks[a.name]}")
        weights = None
        if not problems:
            try:
                weights = self.hub.get_weights(env.weights, self.names)
            except Exception as exc:
                problems.append(f"weights blob {env.weights}: {exc}")
            else:
                problems += weights_problems(weights, self.model_spec)
        if problems:
            raise RoundFailed(f"envelope from {a.name}/{env.site_id} rejected: {problems}")
        if env.client_code_version != flhub_core.code_version():
            log.warning("%s runs flhub_core %s, hub has %s", env.site_id, env.client_code_version,
                        flhub_core.code_version())
        return SiteUpdate(env.site_id, env.framework, weights, env.num_examples, dict(env.metrics),
                          env.client_code_version, env_d)

"""E7 native baseline: Flower's own FedAvg strategy on the central SuperLink, without the hub.

Started by deploy/central/flower/native/run.sh as a Flower run of its own, next to the hub's relay
run (which stays idle; it need not be paused). The hospitals run the same ClientApp as for the hub
(flhub_flower.client_app -> flhub_core.train). The server side is the built-in
``flwr.serverapp.strategy.FedAvg`` (weighted by the replies' "num-examples", float32 accumulation in
reply order: strategy_utils.aggregate_arrayrecords) with three hooks that do not touch aggregation:

- configure_train adds "round" to the config: the hub sends the round in the client config and
  flhub_core.train seeds the data order with it (FedAvg itself only adds "server-round");
- the strategy's Grid lists exactly one SuperNode per configured site (newest node per site, mapped
  with the ClientApp's site-info QUERY like the relay), so stale or E5 nodes are never sampled;
- evaluate_fn checkpoints and evaluates every global model with the hub's own RunDir and Evaluator,
  so <out-dir>/metrics.jsonl and rounds/ have the hub's format (global_ref, agg_state_ref, timings).

A round counts only if every site replied (the hub's strict policy); otherwise the run fails.
"""

from __future__ import annotations

import logging
import random
import time
from importlib.metadata import version
from pathlib import Path

import torch
from flhub_core.data import dataset_info
from flhub_core.model import init_weights, num_parameters
from flhub_core.weights import sha256_ref, to_safetensors
from flwr.app import ArrayRecord, ConfigRecord, Context, Message
from flwr.serverapp import Grid, ServerApp
from flwr.serverapp.strategy import FedAvg

import flhub_core
from flhub_flower.records import ARRAYS, METRICS, SITE_INFO, arrayrecord_to_weights, weights_to_arrayrecord
from flhub_flower.server_app import Relay
from hub.orchestrator.checkpoint import RunDir, utc_now
from hub.orchestrator.config import load_config
from hub.orchestrator.run import Runner, deploy_versions, git_state, make_evaluator, manifest

log = logging.getLogger("flwr.flhub.native")

app = ServerApp()

STATE: dict = {}  # Flower's FedAvg keeps no server state (checkpointed like the hub's fedavg state)
AGGREGATION = "flwr.serverapp.strategy.FedAvg: sum_k (n_k/N) w_k in float32, reply order"


class SiteGrid:
    """The run's Grid as the strategy sees it: one SuperNode per configured site."""

    def __init__(self, grid: Grid, node_ids: list[int]) -> None:
        self._grid, self._nodes = grid, list(node_ids)

    def get_node_ids(self) -> list[int]:
        return list(self._nodes)

    def __getattr__(self, name: str):
        return getattr(self._grid, name)


def map_sites(grid: Grid, sites: list[str], timeout_s: float) -> dict[str, int]:
    """site -> node id via the ClientApp's site-info QUERY (the relay's mapping; newest node wins)."""
    mapper = Relay(grid, client=None, query_timeout_s=60.0)
    end = time.monotonic() + timeout_s
    while True:
        mapper.refresh_nodes(force=True)
        nodes = mapper.node_of_site()
        missing = [s for s in sites if s not in nodes]
        if not missing:
            return {s: nodes[s] for s in sites}
        if time.monotonic() > end:
            raise RuntimeError(f"sites {missing} have no connected SuperNode after {timeout_s:.0f} s")
        time.sleep(1.0)


def site_update(reply: Message) -> dict:
    """One site's reply as a hub-style update record (weights as sha256 of their safetensors)."""
    info = reply.content[SITE_INFO] if SITE_INFO in reply.content else {}
    metrics = dict(reply.content[METRICS])
    n = int(metrics.pop("num-examples"))
    weights = arrayrecord_to_weights(reply.content[ARRAYS])
    return {"site_id": str(info.get("site-id", reply.metadata.src_node_id)), "framework": "flower",
            "adapter": "native", "num_examples": n, "metrics": metrics,
            "client_code_version": str(info.get("client-code-version", "unknown")),
            "node_id": reply.metadata.src_node_id, "reply_created_at": reply.metadata.created_at,
            "weights": sha256_ref(to_safetensors(weights))}


class NativeFedAvg(FedAvg):
    """Flower's FedAvg plus measurement hooks; selection and aggregation are FedAvg's own."""

    def __init__(self, sites: list[str], rd: RunDir, evaluator, **kwargs) -> None:
        super().__init__(**kwargs)
        self.sites, self.rd, self.evaluator = list(sites), rd, evaluator
        self.t_round = self.t_dispatch = self.t_replies = self.agg_s = 0.0
        self.utc_start = ""
        self.updates: list[dict] = []

    def configure_train(self, server_round: int, arrays: ArrayRecord, config: ConfigRecord, grid: Grid):
        config["round"] = server_round  # as in the hub's client config: seeds flhub_core.train's data order
        self.t_round, self.utc_start = time.monotonic(), utc_now(ms=True)
        messages = super().configure_train(server_round, arrays, config, grid)
        self.t_dispatch = time.monotonic()  # Grid.send_and_receive pushes them right after this
        return messages

    def aggregate_train(self, server_round: int, replies):
        self.t_replies = time.monotonic()
        replies = list(replies)
        good = [r for r in replies if not r.has_error()]
        if len(good) < len(self.sites):
            errors = [f"{r.metadata.src_node_id}: {r.error.reason}" for r in replies if r.has_error()]
            raise RuntimeError(f"round {server_round}: {len(good)}/{len(self.sites)} sites replied; {errors}")
        self.updates = sorted((site_update(r) for r in good), key=lambda u: u["site_id"])
        t0 = time.monotonic()
        out = super().aggregate_train(server_round, good)
        self.agg_s = time.monotonic() - t0
        return out

    def evaluate_round(self, server_round: int, arrays: ArrayRecord) -> None:
        """evaluate_fn: checkpoint and evaluate every global model the way the hub does."""
        weights = arrayrecord_to_weights(arrays)
        t0 = time.monotonic()
        m = self.evaluator(weights) if self.evaluator else {}
        eval_s = time.monotonic() - t0
        if server_round == 0:
            rec = self.rd.save_round(0, weights, STATE, {"participants": [], "utc_end": utc_now(ms=True)})
            self.rd.append_metrics(0, {**m, "global_ref": rec["global_ref"], "agg_state_ref": rec["agg_state_ref"]})
            return None
        duration = self.t_replies - self.t_dispatch
        metrics = {**m, "round/duration_s": duration, "round/agg_s": self.agg_s, "round/eval_s": eval_s,
                   "round/num_sites": len(self.updates), "round/num_participants": len(self.sites),
                   "round/num_dropped": 0, "round/attempts": 1,
                   "round/num_examples": sum(u["num_examples"] for u in self.updates)}
        for u in self.updates:
            metrics[f"site/{u['site_id']}/num_examples"] = u["num_examples"]
            for k, v in u["metrics"].items():
                metrics[f"site/{u['site_id']}/{k}"] = v
        record = {"participants": self.sites, "sites": [u["site_id"] for u in self.updates], "dropped": [],
                  "failures": [], "utc_start": self.utc_start, "updates": self.updates, "aggregation": AGGREGATION,
                  "timing": {"duration_s": duration, "agg_s": self.agg_s, "eval_s": eval_s}}
        rec = self.rd.save_round(server_round, weights, STATE, {**record, "utc_end": utc_now(ms=True)})
        metrics.update({"round/total_s": time.monotonic() - self.t_round, "global_ref": rec["global_ref"],
                        "agg_state_ref": rec["agg_state_ref"], "participants": ",".join(record["sites"])})
        self.rd.append_metrics(server_round, metrics)
        acc = m.get("global/test_acc", m.get("global/val_acc"))
        log.info("native round %d | %d sites | %.1fs | agg %.3fs eval %.1fs%s", server_round, len(self.updates),
                 duration, self.agg_s, eval_s, "" if acc is None else f" | acc {acc:.4f}")
        return None  # metrics live in metrics.jsonl


def config_json(cfg, name: str, sites: list[str], nodes: dict[str, int]) -> dict:
    m, msha = manifest(cfg)
    return {"run_name": name, "kind": "native", "mode": "native", "experiment": cfg.experiment,
            "run_config": cfg.to_dict(), "train_config": cfg.train_config, "rounds": cfg.rounds,
            "model_seed": cfg.seed, "dataset": cfg.dataset, "dataset_sha256": dataset_info(cfg.dataset).sha256,
            "data_variant": cfg.data_variant, "partition_manifest_sha256": msha,
            "partition_sizes": {s: m["sites"][s]["n"] for s in sites} if m else None,
            "model": cfg.model, "model_params": num_parameters(cfg.model, cfg.num_classes),
            "aggregator": {"name": "flower-fedavg", "impl": AGGREGATION}, "participation": cfg.participation,
            "deadline_s": cfg.deadline_s, "netem": cfg.netem, "sites": {s: "online" for s in sites},
            "native_nodes": nodes, "framework_versions": {"flower": version("flwr")}, **git_state(),
            "flhub_core_version": flhub_core.code_version(), "torch_version": torch.__version__,
            "deploy_versions": deploy_versions(), "config_file": cfg.source, "started_utc": utc_now()}


@app.main()
def main(grid: Grid, context: Context) -> None:
    run_cfg = context.run_config
    cfg = load_config(str(run_cfg["config"]))
    rd = RunDir(Path(str(run_cfg["out-dir"])))
    if (rd.path / "metrics.jsonl").exists():
        raise RuntimeError(f"{rd.path} already holds a run")
    rd.rounds.mkdir(parents=True, exist_ok=True)
    sites = list(cfg.sites)
    random.seed(cfg.seed)  # FedAvg samples with random.sample; with fraction 1.0 it takes every site
    nodes = map_sites(grid, sites, cfg.online_wait_s)
    rd.write_json("config.json", config_json(cfg, cfg.name, sites, nodes))
    log.info("native FedAvg %s: sites %s, %d rounds -> %s", cfg.name, nodes, cfg.rounds, rd.path)
    strategy = NativeFedAvg(sites, rd, make_evaluator(cfg), fraction_train=1.0, fraction_evaluate=0.0,
                            min_train_nodes=len(sites), min_evaluate_nodes=0, min_available_nodes=len(sites),
                            weighted_by_key="num-examples")
    t0 = time.time()
    try:
        strategy.start(grid=SiteGrid(grid, list(nodes.values())),
                       initial_arrays=weights_to_arrayrecord(init_weights(cfg.seed, cfg.model, cfg.num_classes)),
                       num_rounds=cfg.rounds, timeout=cfg.deadline_s, train_config=ConfigRecord(dict(cfg.train_config)),
                       evaluate_fn=strategy.evaluate_round)
    except BaseException as exc:
        rd.event("run_failed", error=f"{type(exc).__name__}: {exc}")
        rd.write_json("summary.json", {"status": "failed", "error": f"{type(exc).__name__}: {exc}",
                                       "run_dir": str(rd.path), "utc": utc_now()})
        raise
    rd.write_json("summary.json", {**Runner.summary(rd, time.time() - t0), "kind": "native"})
    log.info("native FedAvg %s finished: %s", cfg.name, rd.path)

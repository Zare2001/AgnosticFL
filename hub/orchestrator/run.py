"""The round loop shared by the orchestrator and the reference simulation.

Each round: store the global model, let the executor train it at the sites, FedAvg the
updates, evaluate on the central test split, log, and checkpoint into the run dir
(~/flhub-runtime/runs/<name>-<UTC stamp>/: config.json, metrics.jsonl, rounds/rNNNN.*, summary.json).
"""

from __future__ import annotations

import json
import logging
import platform
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import torch
from flhub_core.data import DATASET, DATASET_SHA256, PARTITION_SHA256, load_test
from flhub_core.model import init_weights, num_parameters
from flhub_core.train import evaluate
from flhub_core.weights import sha256_ref, spec, to_safetensors

import flhub_core
from adapters.common import PROTOCOL, HubClient

from .config import RunConfig
from .executors import RoundFailed
from .fedavg import fedavg
from .tracking import Tracker

log = logging.getLogger("flhub.orchestrator")
REPO = Path(__file__).resolve().parents[2]


def git_state() -> dict:
    def git(*args):
        return subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True).stdout.strip()

    return {"git_sha": git("rev-parse", "HEAD") or "unknown", "git_dirty": bool(git("status", "--porcelain", "-uno"))}


def deploy_versions() -> dict:
    out = {}
    path = REPO / "deploy" / "versions.env"
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.split("#", 1)[0].strip()
            if "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    return out


def make_run_dir(runs_dir: str, name: str) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    root = Path(runs_dir).expanduser()
    run_dir = root / f"{name}-{stamp}"
    (run_dir / "rounds").mkdir(parents=True, exist_ok=False)
    latest = root / f"{name}-latest"
    if latest.is_symlink() or latest.exists():
        latest.unlink()
    latest.symlink_to(run_dir.name)
    return run_dir


class Runner:
    def __init__(self, cfg: RunConfig, executor, *, mode: str, name: str, wandb_mode: str = "offline",
                 tags: list[str] | None = None, group: str | None = None, hub: HubClient | None = None) -> None:
        self.cfg, self.executor, self.mode, self.name = cfg, executor, mode, name
        self.wandb_mode, self.tags, self.group, self.hub = wandb_mode, list(tags or []), group, hub

    def run(self) -> dict:
        cfg = self.cfg
        weights = init_weights(cfg.seed)
        names = list(weights)
        if self.hub:
            self.hub.health()
            self.hub.put_spec(spec(weights))
        info = self.executor.prepare(cfg.online_wait_s)
        test = load_test()
        run_dir = make_run_dir(cfg.runs_dir, self.name)
        config = {
            "run_name": self.name, "mode": self.mode, "rounds": cfg.rounds, "model_seed": cfg.seed,
            "train_config": cfg.train_config, "sites": info["sites"], "framework_versions": info["frameworks"],
            "adapters": [a.__dict__ for a in cfg.adapters] if self.mode == "adapters" else [],
            "protocol": PROTOCOL, **git_state(), "flhub_core_version": flhub_core.code_version(),
            "dataset": DATASET, "dataset_sha256": DATASET_SHA256,
            "partition_sha256": {s: PARTITION_SHA256[s] for s in info["sites"] if s in PARTITION_SHA256},
            "model_params": num_parameters(), "torch_version": torch.__version__,
            "python_version": platform.python_version(), "deploy_versions": deploy_versions(),
            "config_file": cfg.source,
        }
        (run_dir / "config.json").write_text(json.dumps(config, indent=1))
        tracker = Tracker(run_dir, self.wandb_mode, self.name, config, cfg.wandb_project, cfg.wandb_entity,
                          self.group, self.tags, job_type=self.mode)
        log.info("run %s -> %s", self.name, run_dir)
        t_start = time.time()
        try:
            self._save(run_dir, 0, weights, None)
            m = evaluate(weights, test)
            tracker.log(0, {"global/test_loss": m["loss"], "global/test_acc": m["acc"], "global/test_auc": m["auc"]})
            for rnd in range(1, cfg.rounds + 1):
                weights, m = self._round(rnd, weights, names, test, run_dir, tracker)
            summary = {"status": "finished", "rounds": cfg.rounds, "final/test_loss": m["loss"],
                       "final/test_acc": m["acc"], "final/test_auc": m["auc"],
                       "final/global_ref": sha256_ref(to_safetensors(weights)),
                       "duration_s": round(time.time() - t_start, 1), "run_dir": str(run_dir),
                       "wandb_id": tracker.run_id, "wandb_url": tracker.url}
            tracker.summary({k: v for k, v in summary.items() if k not in ("run_dir", "wandb_id", "wandb_url")})
            (run_dir / "summary.json").write_text(json.dumps(summary, indent=1))
            tracker.finish(ok=True)
            return summary
        except BaseException as exc:
            failed = {"status": "failed", "error": f"{type(exc).__name__}: {exc}", "run_dir": str(run_dir),
                      "wandb_id": tracker.run_id}
            (run_dir / "summary.json").write_text(json.dumps(failed, indent=1))
            tracker.summary({"status": "failed"})
            tracker.finish(ok=False)
            raise

    def _round(self, rnd, weights, names, test, run_dir, tracker):
        model_bytes = to_safetensors(weights)
        model_ref = self.hub.put_blob(model_bytes) if self.hub else sha256_ref(model_bytes)
        t0 = time.time()
        for attempt in range(self.cfg.round_retries + 1):
            try:
                updates = self.executor.run_round(rnd, weights, model_ref)
                break
            except RoundFailed as exc:
                if attempt == self.cfg.round_retries:
                    raise
                log.warning("round %d attempt %d failed, retrying: %s", rnd, attempt + 1, exc)
        new = fedavg([(u.site_id, u.weights, u.num_examples) for u in updates], names)
        duration = time.time() - t0
        m = evaluate(new, test)
        metrics = {"global/test_loss": m["loss"], "global/test_acc": m["acc"], "global/test_auc": m["auc"],
                   "round/duration_s": duration, "round/num_sites": len(updates),
                   "round/num_examples": sum(u.num_examples for u in updates)}
        for u in sorted(updates, key=lambda u: u.site_id):
            metrics[f"site/{u.site_id}/num_examples"] = u.num_examples
            for k, v in u.metrics.items():
                metrics[f"site/{u.site_id}/{k}"] = v
        for fw in sorted({u.framework for u in updates}):
            metrics[f"framework/{fw}/num_sites"] = sum(1 for u in updates if u.framework == fw)
        if rnd == 1:
            tracker.update_config({"partition_sizes": {u.site_id: u.num_examples for u in updates},
                                   "client_code_versions": sorted({u.client_code_version for u in updates})})
        tracker.log(rnd, metrics)
        self._save(run_dir, rnd, new, {"round": rnd, "model_ref": model_ref, "duration_s": duration,
                                       "metrics": metrics,
                                       "updates": [{"site_id": u.site_id, "framework": u.framework,
                                                    "num_examples": u.num_examples, "metrics": u.metrics,
                                                    "client_code_version": u.client_code_version,
                                                    "weights": (u.envelope or {}).get("weights")}
                                                   for u in sorted(updates, key=lambda u: u.site_id)]})
        log.info("round %d/%d | %d sites | %.1fs | test loss %.4f acc %.4f auc %.4f", rnd, self.cfg.rounds,
                 len(updates), duration, m["loss"], m["acc"], m["auc"])
        return new, m

    @staticmethod
    def _save(run_dir: Path, rnd: int, weights, record: dict | None) -> None:
        (run_dir / "rounds" / f"r{rnd:04d}.safetensors").write_bytes(to_safetensors(weights))
        if record is not None:
            record["global_ref"] = sha256_ref(to_safetensors(weights))
            (run_dir / "rounds" / f"r{rnd:04d}.json").write_text(json.dumps(record, indent=1))

"""The round loop shared by the orchestrator (real runs) and hub.sim (reference runs).

Per round: sample the participants, store the global model in the hub core, let the executor
train it (retrying a failed round after the sites are back online), aggregate with the run's
stateful aggregator, evaluate on the central split, and checkpoint weights, aggregator state
and the round record into the run dir (hub/orchestrator/checkpoint.py). No live W&B logging:
scripts/stage2/finalize_run.py uploads finished runs after their gate passed.
"""

from __future__ import annotations

import json
import logging
import platform
import statistics
import subprocess
import time
from pathlib import Path

import torch
from flhub_core.data import DATASETS, PARTITION_SHA256, dataset_info
from flhub_core.model import init_weights, is_payload, num_parameters
from flhub_core.weights import sha256_ref, spec, to_safetensors

import flhub_core
from adapters.common import PROTOCOL, HubClient

from .checkpoint import RunDir, utc_now
from .config import REPO, RunConfig
from .evaluation import Evaluator
from .executors import RoundFailed
from .participation import sample

log = logging.getLogger("flhub.orchestrator")
MANIFEST_DIR = REPO / "configs" / "stage2" / "partitions"


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


def manifest(cfg: RunConfig) -> tuple[dict | None, str | None]:
    """(manifest dict, sha256 of the manifest file) of the run's data variant."""
    if not cfg.data_variant:
        return None, None
    path = MANIFEST_DIR / f"{cfg.data_variant}.json"
    raw = path.read_bytes()
    import hashlib

    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def make_evaluator(cfg: RunConfig, split: str | None = None) -> Evaluator | None:
    if is_payload(cfg.model) or not cfg.eval.get("every"):
        return None
    m, _ = manifest(cfg)
    hists = None
    if cfg.eval.get("site_shaped") and m:
        hists = {s: m["sites"][s]["label_hist"] for s in cfg.sites if s in m["sites"]}
    return Evaluator(cfg.dataset, cfg.model, cfg.num_classes, split or cfg.eval.get("split", "test"), hists,
                     threads=int(cfg.eval.get("threads", 1)))


class Runner:
    def __init__(self, cfg: RunConfig, executor, *, kind: str, name: str, hub: HubClient | None = None,
                 evaluator: Evaluator | None = None, participants: dict[int, list[str]] | None = None,
                 resume: RunDir | None = None, extra: dict | None = None) -> None:
        self.cfg, self.executor, self.kind, self.name, self.hub = cfg, executor, kind, name, hub
        self.evaluator = evaluator
        self.override = participants or {}
        self.resume = resume
        self.extra = extra or {}
        self.agg = cfg.aggregator_obj()

    def participants(self, rnd: int) -> list[str]:
        if rnd in self.override:
            return list(self.override[rnd])
        p = self.cfg.participation
        return sample(self.cfg.sites, float(p.get("fraction", 1.0)), int(p.get("seed", self.cfg.seed)), rnd)

    def _config_json(self, info: dict) -> dict:
        cfg = self.cfg
        m, msha = manifest(cfg)
        return {
            "run_name": self.name, "kind": self.kind, "mode": cfg.mode, "experiment": cfg.experiment,
            "run_config": cfg.to_dict(), "train_config": cfg.train_config, "rounds": cfg.rounds,
            "model_seed": cfg.seed, "dataset": cfg.dataset, "dataset_sha256": dataset_info(cfg.dataset).sha256,
            "data_variant": cfg.data_variant, "partition_manifest_sha256": msha,
            "partition_sizes": {s: m["sites"][s]["n"] for s in cfg.sites} if m else None,
            "stage1_partition_sha256": {s: PARTITION_SHA256[s] for s in cfg.sites if s in PARTITION_SHA256}
            if not m else None,
            "model": cfg.model, "model_params": num_parameters(cfg.model, cfg.num_classes),
            "aggregator": cfg.aggregator, "participation": cfg.participation, "deadline_s": cfg.deadline_s,
            "round_retries": cfg.round_retries, "netem": cfg.netem, "sites": info.get("sites"),
            "framework_versions": info.get("frameworks"), "protocols": info.get("protocols"),
            "protocol": PROTOCOL, **git_state(), "flhub_core_version": flhub_core.code_version(),
            "torch_version": torch.__version__, "python_version": platform.python_version(),
            "deploy_versions": deploy_versions(), "config_file": cfg.source, "datasets": sorted(DATASETS),
            "started_utc": utc_now(), **self.extra,
        }

    def run(self) -> dict:
        cfg = self.cfg
        w0 = init_weights(cfg.seed, cfg.model, cfg.num_classes)
        names = list(w0)
        if self.hub:
            self.hub.health()
            self.hub.put_spec(spec(w0))
        info = self.executor.prepare(cfg.sites, cfg.online_wait_s)
        if self.resume:
            rd = self.resume
            start = rd.last_complete_round()
            if start is None:
                raise RuntimeError(f"{rd.path}: no complete round to resume from")
            weights, state = rd.load_weights(start, names), rd.load_state(start)
            rd.truncate_metrics(start)
            rd.event("resume", from_round=start, sites=info.get("sites"))
            log.info("resuming %s after round %d", rd.path, start)
        else:
            rd = RunDir.create(cfg.runs_dir, self.name)
            rd.write_json("config.json", self._config_json(info))
            weights, state, start = w0, self.agg.init(w0), 0
            rec = rd.save_round(0, weights, state, {"participants": [], "utc_end": utc_now(ms=True)})
            m0 = self.evaluator(weights) if self.evaluator else {}
            rd.append_metrics(0, {**m0, "global_ref": rec["global_ref"], "agg_state_ref": rec["agg_state_ref"]})
        self.rd = rd
        log.info("run %s -> %s", self.name, rd.path)
        t_start = time.time()
        try:
            for rnd in range(start + 1, cfg.rounds + 1):
                weights, state = self._round(rnd, weights, state)
                self._hold(rnd)
        except BaseException as exc:
            rd.event("run_failed", error=f"{type(exc).__name__}: {exc}")
            rd.write_json("summary.json", {"status": "failed", "error": f"{type(exc).__name__}: {exc}",
                                           "run_dir": str(rd.path), "utc": utc_now()})
            raise
        finally:
            close = getattr(self.executor, "close", None)
            if close:
                close()
        summary = self.summary(rd, time.time() - t_start)
        rd.write_json("summary.json", summary)
        return summary

    def _round(self, rnd: int, weights, state):
        cfg, rd = self.cfg, self.rd
        t_round, utc_start = time.monotonic(), utc_now(ms=True)
        participants = self.participants(rnd)
        model_bytes = to_safetensors(weights)
        model_ref = self.hub.put_blob(model_bytes) if self.hub else sha256_ref(model_bytes)
        failures = []
        if cfg.min_sites is None and rnd > 1:  # strict: never dispatch to a site known to be offline
            waited = self.executor.wait_online(participants, cfg.online_wait_s)
            if waited > 5:
                rd.event("waited_online", round=rnd, seconds=round(waited, 1))
        while True:
            try:
                outcome = self.executor.run_round(rnd, weights, model_ref, participants, cfg.min_sites)
                break
            except RoundFailed as exc:
                failures.append({"utc": utc_now(ms=True), "error": str(exc)[:2000]})
                rd.event("round_failed", round=rnd, attempt=len(failures), error=str(exc)[:2000])
                log.warning("round %d attempt %d failed: %s", rnd, len(failures), exc)
                if len(failures) > cfg.round_retries:
                    raise
                waited = self.executor.wait_online(participants, cfg.online_wait_s)
                rd.event("round_retry", round=rnd, attempt=len(failures) + 1, waited_online_s=round(waited, 1))
        t_agg = time.monotonic()
        results = [(u.site_id, u.weights, u.num_examples, u.metrics) for u in outcome.updates]
        new, new_state = self.agg.aggregate(weights, results, state, rnd)
        agg_s = time.monotonic() - t_agg
        t_eval = time.monotonic()
        every = int(cfg.eval.get("every") or 0)
        m = self.evaluator(new) if self.evaluator and every and (rnd % every == 0 or rnd == cfg.rounds) else {}
        eval_s = time.monotonic() - t_eval
        metrics = {**m, "round/duration_s": outcome.duration, "round/agg_s": agg_s, "round/eval_s": eval_s,
                   "round/num_sites": len(outcome.updates), "round/num_participants": len(participants),
                   "round/num_dropped": len(outcome.dropped), "round/attempts": len(failures) + 1,
                   "round/num_examples": sum(u.num_examples for u in outcome.updates)}
        updates = []
        for u in sorted(outcome.updates, key=lambda u: u.site_id):
            t_disp = (outcome.adapters.get(u.adapter) or {}).get("t_dispatch", 0.0)
            train_s = u.metrics.get("train_time_s")
            overhead = (u.t_result - t_disp - train_s) if (u.t_result is not None and train_s is not None) else None
            metrics[f"site/{u.site_id}/num_examples"] = u.num_examples
            for k, v in u.metrics.items():
                metrics[f"site/{u.site_id}/{k}"] = v
            if u.t_result is not None:
                metrics[f"site/{u.site_id}/t_result_s"] = u.t_result
            if overhead is not None:
                metrics[f"site/{u.site_id}/overhead_s"] = overhead
            updates.append({"site_id": u.site_id, "framework": u.framework, "adapter": u.adapter,
                            "num_examples": u.num_examples, "metrics": u.metrics, "t_result_s": u.t_result,
                            "overhead_s": overhead, "client_code_version": u.client_code_version,
                            "weights": (u.envelope or {}).get("weights")})
        for name, t in outcome.adapters.items():
            metrics[f"adapter/{name}/t_dispatch_s"] = t["t_dispatch"]
            if t.get("fanout") is not None:
                metrics[f"adapter/{name}/fanout_s"] = t["fanout"]
        record = {"model_ref": model_ref, "participants": participants,
                  "sites": [u["site_id"] for u in updates], "dropped": outcome.dropped, "failures": failures,
                  "utc_start": utc_start, "adapters": outcome.adapters, "updates": updates,
                  "timing": {"duration_s": outcome.duration, "agg_s": agg_s, "eval_s": eval_s}}
        record = rd.save_round(rnd, new, new_state, {**record, "utc_end": utc_now(ms=True)})
        metrics.update({"round/total_s": time.monotonic() - t_round, "global_ref": record["global_ref"],
                        "agg_state_ref": record["agg_state_ref"], "participants": ",".join(record["sites"])})
        rd.append_metrics(rnd, metrics)
        if outcome.dropped:
            rd.event("sites_dropped", round=rnd, dropped=outcome.dropped)
        if self.hub and cfg.retention.get("blobs") == "rolling":
            self._gc_blobs(model_ref, updates)
        if cfg.retention.get("checkpoints") == "latest" and rnd > 1:
            # payload runs: the hashes in metrics.jsonl suffice for INV-1; keep only what resume needs
            rd.apply_retention({0, rnd})
        acc = m.get("global/test_acc", m.get("global/val_acc"))
        log.info("round %d/%d | %d/%d sites | %.1fs | agg %.2fs eval %.1fs%s", rnd, cfg.rounds, len(updates),
                 len(participants), outcome.duration, agg_s, eval_s, "" if acc is None else f" | acc {acc:.4f}")
        return new, new_state

    def _hold(self, rnd: int, max_s: float = 3600.0) -> None:
        """Fault-injection hook (E6e): wait at this round boundary while <run_dir>/hold-after-rNNNN exists."""
        flag = self.rd.path / f"hold-after-r{rnd:04d}"
        if not flag.exists():
            return
        t0 = time.monotonic()
        self.rd.event("hold", round=rnd)
        while flag.exists() and time.monotonic() - t0 < max_s:
            time.sleep(0.5)
        self.rd.event("hold_released", round=rnd, seconds=round(time.monotonic() - t0, 1))

    def _gc_blobs(self, model_ref: str, updates: list[dict]) -> None:
        for ref in [model_ref] + [u["weights"] for u in updates if u.get("weights")]:
            try:
                self.hub.delete_blob(ref)
            except Exception as exc:  # GC must never fail a round
                log.warning("blob GC of %s failed: %s", ref, exc)

    @staticmethod
    def summary(rd: RunDir, duration_s: float) -> dict:
        rows = rd.metrics()
        last = rd.last_complete_round()
        evaluated = [r for r in rows if any(k.startswith("global/") for k in r)]
        final = evaluated[-1] if evaluated else {}
        times = [r["round/duration_s"] for r in rows if r.get("step", 0) >= 2 and "round/duration_s" in r]
        rec = rd.record(last)
        out = {"status": "finished", "rounds": last, "run_dir": str(rd.path), "utc_end": utc_now(),
               "duration_s": round(duration_s, 1), "final/global_ref": rec["global_ref"],
               "final/agg_state_ref": rec["agg_state_ref"], "final/eval_round": final.get("step"),
               "round_time_median_s": statistics.median(times) if times else None,
               "dropped_sites_total": sum(r.get("round/num_dropped", 0) for r in rows),
               "retries_total": sum(r.get("round/attempts", 1) - 1 for r in rows if r.get("step", 0) > 0),
               "resumed": any(e["event"] == "resume" for e in rd.events())}
        for k, v in final.items():
            if k.startswith("global/"):
                out["final/" + k[len("global/"):]] = v
            elif k in ("siteshaped/min_acc", "siteshaped/mean_acc", "siteshaped/std_acc"):
                out["final/siteshaped_" + k.split("/")[1]] = v
        return out


def run_dir_path(path: str | Path) -> RunDir:
    return RunDir(Path(path).expanduser())

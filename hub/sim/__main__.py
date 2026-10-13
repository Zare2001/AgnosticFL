"""Framework-free reference runs (hub.sim): the same config, flhub_core.train and aggregator as the hub.

    python -m hub.sim --config C [--seed N] [--name N] [--workers N] [--split test|val] [--rounds N]
                      [--participants-from REAL_RUN_DIR] [--compare REAL_RUN_DIR] [--runs-dir D]
    python -m hub.sim --config C --mode centralized [--rounds EPOCHS]     pooled train split, 1 epoch/round
    python -m hub.sim --config C --mode local [--rounds EPOCHS]           every site alone, one run
    python -m hub.sim --replay REAL_RUN_DIR [--rounds 1,2,3,last] [--workers N]

federated (default): every participant trains in this process or in N worker processes
(deterministic result order); --participants-from replays a real run's per-round participants;
--compare writes the INV-1 comparison with that real run (inv1.json in both run dirs).
--replay recomputes the listed rounds of a real run from its own previous global weights and
aggregator state (INV-1 replay spot-check) and writes <run_dir>/inv1-replay.json.
"""

from __future__ import annotations

import argparse
import json
import logging
import multiprocessing
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from flhub_core.model import init_weights
from flhub_core.weights import max_abs_diff, sha256_ref, to_safetensors

from hub.aggregators import state_ref
from hub.orchestrator.checkpoint import RunDir, utc_now
from hub.orchestrator.config import config_from_dict, load_config
from hub.orchestrator.executors import DirectExecutor, _worker_init, load_site_data
from hub.orchestrator.inv1 import compare_runs
from hub.orchestrator.run import Runner, make_evaluator

log = logging.getLogger("flhub.sim")


def data_spec(cfg, data_dir: str | None = None) -> tuple:
    return ("variant", cfg.data_variant) if cfg.data_variant else ("stage1", data_dir)


def participants_from(path: str) -> dict[int, list[str]]:
    rd = RunDir(path)
    return {r: rd.record(r)["sites"] for r in rd.recorded_rounds() if r > 0}


def run_federated(cfg, args) -> dict:
    executor = DirectExecutor(data_spec(cfg, args.data_dir), cfg.train_config, "sim", args.workers)
    override = participants_from(args.participants_from) if args.participants_from else None
    extra = {"sim_of": args.participants_from or args.compare, "workers": args.workers}
    runner = Runner(cfg, executor, kind="sim", name=args.name, evaluator=make_evaluator(cfg, args.split),
                    participants=override, extra=extra)
    summary = runner.run()
    if args.compare:
        res = compare_runs(RunDir(args.compare), runner.rd)
        res["compared_utc"] = utc_now()
        runner.rd.write_json("inv1.json", res)
        RunDir(args.compare).write_json("inv1.json", res)
        summary["inv1"] = {k: res[k] for k in ("method", "hashes_equal", "max_abs_diff", "pass")}
        print(f"INV-1 full sim vs {args.compare}: hashes_equal={res['hashes_equal']} "
              f"max_abs_diff={res['max_abs_diff']:.3e} -> {'PASS' if res['pass'] else 'FAIL'}")
    return summary


def run_centralized(cfg, args) -> dict:
    cfg.sites = ["pooled"]
    cfg.participation = {"fraction": 1.0, "seed": cfg.seed, "min_sites": None}
    cfg.aggregator = {"name": "fedavg", "params": {}}
    executor = DirectExecutor(("pooled", cfg.dataset), cfg.train_config, "centralized", 0)
    return Runner(cfg, executor, kind="centralized", name=args.name, evaluator=make_evaluator(cfg, args.split)).run()


def _local_site(job: tuple) -> tuple:
    cfg, site, spec, split = job
    from flhub_core.train import train

    data = load_site_data(spec, site)
    evaluator = make_evaluator(cfg, split)
    w = init_weights(cfg.seed, cfg.model, cfg.num_classes)
    rows = []
    for epoch in range(1, cfg.rounds + 1):
        t0 = time.monotonic()
        w, n, m = train(w, data, {**cfg.train_config, "round": epoch})
        ev = evaluator(w) if evaluator else {}
        rows.append({"epoch": epoch, "n": n, "train_s": time.monotonic() - t0, "global_ref": sha256_ref(
            to_safetensors(w)), **{k: v for k, v in m.items() if k in ("train_loss", "train_acc")}, **ev})
    return site, rows


def run_local(cfg, args) -> dict:
    """Each site trains alone for `rounds` epochs (E1 local-only reference); one run dir for all sites."""
    spec = data_spec(cfg, args.data_dir)
    rd = RunDir.create(cfg.runs_dir, args.name)
    rd.write_json("config.json", {"run_name": args.name, "kind": "local", "mode": cfg.mode,
                                  "experiment": cfg.experiment, "run_config": cfg.to_dict(),
                                  "started_utc": utc_now()})
    jobs = [(cfg, s, spec, args.split) for s in cfg.sites]
    if args.workers > 1:
        with ProcessPoolExecutor(args.workers, mp_context=multiprocessing.get_context("spawn"),
                                 initializer=_worker_init) as pool:
            results = dict(pool.map(_local_site, jobs))
    else:
        results = dict(_local_site(j) for j in jobs)
    key = f"global/{args.split}_acc"
    for epoch in range(1, cfg.rounds + 1):
        row = {}
        for site, rows in results.items():
            for k, v in rows[epoch - 1].items():
                if k not in ("epoch", "global_ref"):
                    row[f"local/{site}/{k.replace('global/', '')}"] = v
        accs = [results[s][epoch - 1].get(key) for s in results]
        if all(a is not None for a in accs):
            row.update({"local/mean_acc": sum(accs) / len(accs), "local/min_acc": min(accs),
                        "local/max_acc": max(accs)})
        rd.append_metrics(epoch, row)
    last = rd.metrics()[-1]
    summary = {"status": "finished", "rounds": cfg.rounds, "run_dir": str(rd.path), "utc_end": utc_now(),
               "final/mean_acc": last.get("local/mean_acc"), "final/min_acc": last.get("local/min_acc"),
               "final/max_acc": last.get("local/max_acc")}
    rd.write_json("summary.json", summary)
    return summary


def replay(path: str, rounds_arg: str, workers: int, data_dir: str | None = None) -> dict:
    """INV-1 replay spot-check of a real run: recompute rounds from its own checkpoints."""
    rd = RunDir(path)
    saved = rd.read_json("config.json")
    cfg = config_from_dict(saved["run_config"])
    last = rd.last_complete_round()
    rounds = sorted({last if x.strip() == "last" else int(x) for x in rounds_arg.split(",")})
    agg = cfg.aggregator_obj()
    executor = DirectExecutor(data_spec(cfg, data_dir), cfg.train_config, "replay", workers)
    names = list(init_weights(cfg.seed, cfg.model, cfg.num_classes))
    per_round = []
    try:
        for r in rounds:
            rec = rd.record(r)
            w_prev, s_prev = rd.load_weights(r - 1, names), rd.load_state(r - 1)
            if sha256_ref(to_safetensors(w_prev)) != rd.record(r - 1)["global_ref"]:
                raise RuntimeError(f"round {r - 1} checkpoint does not match its recorded hash")
            t0 = time.time()
            outcome = executor.run_round(r, w_prev, rec["model_ref"], rec["sites"])
            results = [(u.site_id, u.weights, u.num_examples, u.metrics) for u in outcome.updates]
            new, new_state = agg.aggregate(w_prev, results, s_prev, r)
            ref = sha256_ref(to_safetensors(new))
            diff = 0.0 if ref == rec["global_ref"] else max_abs_diff(new, rd.load_weights(r, names))
            per_round.append({"round": r, "hash_equal": ref == rec["global_ref"], "max_abs_diff": diff,
                              "state_equal": state_ref(new_state) == rec["agg_state_ref"],
                              "sites": rec["sites"], "seconds": round(time.time() - t0, 1)})
            log.info("replay round %d: hash_equal=%s max_abs_diff=%.3e", r, ref == rec["global_ref"], diff)
    finally:
        executor.close()
    worst = max(x["max_abs_diff"] for x in per_round)
    res = {"method": "replay", "run": str(rd.path), "rounds": rounds, "max_abs_diff": worst,
           "hashes_equal": all(x["hash_equal"] for x in per_round), "tolerance": 1e-5, "pass": worst <= 1e-5,
           "per_round": per_round, "compared_utc": utc_now()}
    rd.write_json("inv1-replay.json", res)
    print(f"INV-1 replay {rounds}: hashes_equal={res['hashes_equal']} max_abs_diff={worst:.3e} -> "
          f"{'PASS' if res['pass'] else 'FAIL'}")
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m hub.sim")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--config")
    src.add_argument("--replay", metavar="RUN_DIR")
    ap.add_argument("--mode", default="federated", choices=["federated", "fedavg", "centralized", "local"])
    ap.add_argument("--seed", type=int)
    ap.add_argument("--name")
    ap.add_argument("--workers", type=int, default=0)
    ap.add_argument("--split", default=None, choices=["test", "val"])
    ap.add_argument("--rounds", default=None, help="number of rounds; --replay: list like 1,2,3,last")
    ap.add_argument("--runs-dir")
    ap.add_argument("--data-dir", help="stage-1 site CSVs (default $FLHUB_DATA_DIR)")
    ap.add_argument("--participants-from", metavar="RUN_DIR")
    ap.add_argument("--compare", metavar="RUN_DIR", help="write the INV-1 comparison with this real run")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    if args.replay:
        res = replay(args.replay, args.rounds or "1,2,3,last", args.workers, args.data_dir)
        return 0 if res["pass"] else 1
    cfg = load_config(args.config, rounds=int(args.rounds) if args.rounds else None, runs_dir=args.runs_dir)
    if args.seed is not None:
        cfg = cfg.with_seed(args.seed)
    args.split = args.split or cfg.eval.get("split", "test")
    if args.mode == "centralized":
        args.name = args.name or f"{cfg.name}-centralized"
        summary = run_centralized(cfg, args)
    elif args.mode == "local":
        args.name = args.name or f"{cfg.name}-local"
        summary = run_local(cfg, args)
    else:
        args.name = args.name or (f"{cfg.name}-seed{args.seed}" if args.seed is not None else f"{cfg.name}-sim")
        args.participants_from = args.participants_from or args.compare
        summary = run_federated(cfg, args)
    print(json.dumps(summary, indent=1))
    return 0 if summary.get("inv1", {}).get("pass", True) else 1


if __name__ == "__main__":
    sys.exit(main())

"""python -m hub.orchestrator run --config configs/stage2/<run>.yaml [--name N] [--seed N] [--rounds N]
                                 [--runs-dir D] [--only nvflare ...]
   python -m hub.orchestrator run --resume <run_dir>

Real runs through the framework adapters. Writes the run dir only (config.json, metrics.jsonl,
events.jsonl, rounds/, summary.json); scripts/stage2/finalize_run.py uploads it to W&B after its
gate passed. --resume continues a killed run after its last complete round, with the saved
config, global weights and aggregator state (participant sampling is stateless).
--only (stage-1 configs) restricts the run to some adapters of the config.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from adapters.common import HubClient

from .checkpoint import RunDir
from .config import config_from_dict, load_config
from .executors import AdapterExecutor, RoundFailed
from .run import Runner, make_evaluator, manifest


def build(cfg, hub: HubClient, name: str, resume: RunDir | None = None) -> Runner:
    from flhub_core.model import init_weights
    from flhub_core.weights import spec

    m, _ = manifest(cfg)
    data_fp = {s: m["sites"][s]["fingerprint"] for s in cfg.sites} if m else None
    executor = AdapterExecutor(cfg, hub, spec(init_weights(cfg.seed, cfg.model, cfg.num_classes)), data_fp)
    return Runner(cfg, executor, kind="adapters", name=name, hub=hub, evaluator=make_evaluator(cfg), resume=resume)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m hub.orchestrator")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run federated rounds through the adapters")
    src = r.add_mutually_exclusive_group(required=True)
    src.add_argument("--config")
    src.add_argument("--resume", metavar="RUN_DIR", help="continue a killed run")
    r.add_argument("--name")
    r.add_argument("--seed", type=int)
    r.add_argument("--rounds", type=int)
    r.add_argument("--runs-dir")
    r.add_argument("--only", nargs="+", metavar="ADAPTER", help="use only these adapters of the config")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    if args.resume:
        rd = RunDir(args.resume)
        saved = rd.read_json("config.json")
        cfg = config_from_dict(saved["run_config"])
        name, resume = saved["run_name"], rd
    else:
        cfg = load_config(args.config, rounds=args.rounds, runs_dir=args.runs_dir)
        if args.seed is not None:
            cfg = cfg.with_seed(args.seed)
        if args.only:
            unknown = set(args.only) - {a.name for a in cfg.adapters}
            if unknown:
                ap.error(f"--only: no adapters {sorted(unknown)} in {args.config}")
            cfg.adapters = [a for a in cfg.adapters if a.name in args.only]
            cfg.sites = [s for a in cfg.adapters for s in a.sites]
        name, resume = args.name or cfg.name, None
    hub = HubClient(cfg.hub_url)
    hub.health()
    try:
        summary = build(cfg, hub, name, resume).run()
    except RoundFailed as exc:
        logging.getLogger("flhub.orchestrator").error("run failed: %s", exc)
        return 1
    print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())

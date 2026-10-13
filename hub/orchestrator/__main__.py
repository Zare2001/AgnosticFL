"""python -m hub.orchestrator run --config configs/<name>.yaml [--wandb offline|online|disabled]
                                 [--name N] [--tags final ...] [--group G] [--rounds N] [--runs-dir D]
                                 [--only nvflare ...]

--only restricts the run to some adapters of the config, e.g. the single-framework final runs use
configs/final.yaml with --only nvflare, so every final run shares one training config."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys

from adapters.common import HubClient

from .config import load_config
from .executors import AdapterExecutor, RoundFailed
from .run import Runner


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m hub.orchestrator")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run federated rounds through the adapters")
    r.add_argument("--config", required=True)
    r.add_argument("--wandb", default=os.environ.get("WANDB_MODE", "offline"),
                   choices=["offline", "online", "disabled"])
    r.add_argument("--name")
    r.add_argument("--tags", nargs="*")
    r.add_argument("--group")
    r.add_argument("--rounds", type=int)
    r.add_argument("--runs-dir")
    r.add_argument("--only", nargs="+", metavar="ADAPTER", help="use only these adapters of the config")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    cfg = load_config(args.config, rounds=args.rounds, runs_dir=args.runs_dir)
    if args.only:
        unknown = set(args.only) - {a.name for a in cfg.adapters}
        if unknown:
            ap.error(f"--only: no adapters {sorted(unknown)} in {args.config}")
        cfg.adapters = [a for a in cfg.adapters if a.name in args.only]
    hub = HubClient(cfg.hub_url)
    hub.health()
    from flhub_core.model import init_weights
    from flhub_core.weights import spec

    executor = AdapterExecutor(cfg.adapters, hub, cfg.train_config, spec(init_weights(cfg.seed)),
                               cfg.deadline_s, cfg.poll_interval_s)
    runner = Runner(cfg, executor, mode="adapters", name=args.name or cfg.name, wandb_mode=args.wandb,
                    tags=args.tags if args.tags is not None else cfg.tags, group=args.group or cfg.group, hub=hub)
    try:
        summary = runner.run()
    except RoundFailed as exc:
        logging.getLogger("flhub.orchestrator").error("run failed: %s", exc)
        return 1
    print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())

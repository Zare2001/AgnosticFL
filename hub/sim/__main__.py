"""python -m hub.sim --config configs/<name>.yaml [--mode fedavg|centralized] [--sites ...]
                     [--data-dir D] [--wandb offline|online|disabled] [--name N] [--tags ...] [--group G] [--rounds N]

fedavg       every site trains in-process (DirectExecutor) -> the reference for the equivalence check
centralized  one pseudo-site holding all 6 partitions (= the official train split); a "round" is
             local_epochs epochs over the pooled data, FedAvg of one update is the identity
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys

from flhub_core.data import load_site, load_train_pooled, materialize, site_path

from hub.orchestrator.config import load_config
from hub.orchestrator.executors import DirectExecutor
from hub.orchestrator.run import Runner


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m hub.sim")
    ap.add_argument("--config", required=True)
    ap.add_argument("--mode", default="fedavg", choices=["fedavg", "centralized"])
    ap.add_argument("--sites", nargs="+", help="default: all sites of the config's adapters")
    ap.add_argument("--data-dir")
    ap.add_argument("--wandb", default=os.environ.get("WANDB_MODE", "offline"),
                    choices=["offline", "online", "disabled"])
    ap.add_argument("--name")
    ap.add_argument("--tags", nargs="*")
    ap.add_argument("--group")
    ap.add_argument("--rounds", type=int)
    ap.add_argument("--runs-dir")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    cfg = load_config(args.config, rounds=args.rounds, runs_dir=args.runs_dir)
    if args.mode == "fedavg":
        data = {}
        for site in args.sites or cfg.sites:
            path = site_path(site, args.data_dir)
            data[site] = load_site(path if path.exists() else materialize(site, args.data_dir))
        executor, name, mode = DirectExecutor(data, cfg.train_config, "sim"), "sim-fedavg-reference", "sim-fedavg"
    else:
        executor = DirectExecutor({"pooled": load_train_pooled()}, cfg.train_config, "centralized")
        name, mode = "centralized-baseline", "centralized"
    runner = Runner(cfg, executor, mode=mode, name=args.name or name, wandb_mode=args.wandb,
                    tags=args.tags if args.tags is not None else cfg.tags, group=args.group or cfg.group)
    print(json.dumps(runner.run(), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())

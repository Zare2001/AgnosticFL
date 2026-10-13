"""Generate every stage-2 run config from one place (EXPERIMENTS.md), so names and settings match.

    python scripts/stage2/make_configs.py [--out configs/stage2] [--check]

Tuned values (client lr, server eta per aggregator, FedProx mu, A*) come from
configs/stage2/tuned.yaml once S3 is done; until then the plan's defaults are used.
Writes configs/stage2/{s3,e1,e2,e3,e4a,e4b,e5,e6,e7}/<run name>.yaml; --check fails if a file
would change (used at S3-frozen).
"""

from __future__ import annotations

import argparse
import copy
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
BASE_SITES = ["nvf-h1", "nvf-h2", "v6-h3", "v6-h4", "flw-h5", "flw-h6"]
EXTRA_SITES = ["nvf-h7", "nvf-h8", "v6-h9", "v6-h10", "flw-h11", "flw-h12"]
FW_SITES = {"nvflare": ["nvf-h1", "nvf-h2"], "vantage6": ["v6-h3", "v6-h4"], "flower": ["flw-h5", "flw-h6"]}
AGGS = ["fedavgm", "fedadam", "fedyogi", "fedadagrad", "fedprox"]
GRID = {"lr": [0.01, 0.03, 0.05, 0.1], "fedavgm": [0.1, 0.3, 1.0], "fedadam": [1e-3, 3e-3, 1e-2, 3e-2],
        "fedyogi": [1e-3, 3e-3, 1e-2, 3e-2], "fedadagrad": [1e-2, 3e-2, 1e-1], "fedprox": [0.001, 0.01, 0.1]}
DEFAULT_TUNED = {"lr": 0.05, "fedavgm": 1.0, "fedadam": 1e-2, "fedyogi": 1e-2, "fedadagrad": 1e-1, "fedprox": 0.01,
                 "a_star": "fedadam", "tuned": False}
PAYLOAD_SIZES = [1, 8, 32, 64, 128, 256, 512]


def base(tuned: dict) -> dict:
    return {
        "schema": 2, "mode": "real", "dataset": "organamnist", "partition": "dir0.3", "model": "cnn-small",
        "aggregator": {"name": "fedavg", "params": {}},
        "train_config": {"local_epochs": 1, "batch_size": 64, "lr": tuned["lr"], "momentum": 0.0},
        "seed": 2026, "rounds": 50, "sites": list(BASE_SITES),
        "participation": {"fraction": 1.0, "seed": 2026, "min_sites": None},
        "deadline_s": 1800, "round_retries": 1, "online_wait_s": 600,
        "eval": {"every": 1, "site_shaped": True, "split": "test", "threads": 1},
        "retention": {"keep_every": 10, "blobs": "rolling", "checkpoints": "all"},
        "runs_dir": "~/flhub-runtime/runs/stage2", "netem": "none", "strict_code_version": True,
    }


def agg(name: str, tuned: dict, value=None) -> dict:
    if name == "fedavg":
        return {"name": "fedavg", "params": {}}
    if name == "fedprox":
        return {"name": "fedprox", "params": {"mu": value if value is not None else tuned["fedprox"]}}
    params = {"eta": value if value is not None else tuned[name]}
    if name == "fedavgm":
        params["beta"] = 0.9
    return {"name": name, "params": params}


def make(name: str, exp: str, tuned: dict, **over) -> tuple[str, dict]:
    cfg = base(tuned)
    cfg["name"], cfg["experiment"] = name, exp
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(cfg.get(k), dict) and k != "aggregator":
            cfg[k] = {**cfg[k], **v}
        else:
            cfg[k] = v
    return name, cfg


def configs(tuned: dict) -> dict[str, list[tuple[str, dict]]]:
    out: dict[str, list] = {k: [] for k in ("s3", "e1", "e2", "e3", "e4a", "e4b", "e5", "e6", "e7")}
    a_star = tuned["a_star"]
    # S3 tuning: sim only, validation split, dir0.3, 30 rounds
    s3 = {"mode": "sim", "rounds": 30, "eval": {"split": "val"}, "runs_dir": "~/flhub-runtime/runs/s3"}
    for lr in GRID["lr"]:
        out["s3"].append(make(f"s3-fedavg-lr{lr:g}", "S3", tuned, train_config={**base(tuned)["train_config"],
                                                                                  "lr": lr}, **s3))
    for a in AGGS:
        for v in GRID[a]:
            out["s3"].append(make(f"s3-{a}-{'mu' if a == 'fedprox' else 'eta'}{v:g}", "S3", tuned,
                                  aggregator=agg(a, tuned, v), **s3))
    # E1 data heterogeneity
    for pid in ["iid", "dir1.0", "dir0.3", "dir0.1", "qty", "fwcorr", "mix"]:
        out["e1"].append(make(f"s2-e1-{pid}-fedavg", "E1", tuned, partition=pid))
    out["e1"].append(make("s2-e1-centralized", "E1", tuned, partition="iid", mode="reference",
                          eval={"site_shaped": False}))  # hub.sim --mode centralized: the pooled train split
    for pid in ["iid", "dir0.1"]:
        out["e1"].append(make(f"s2-e1-local-{pid}", "E1", tuned, partition=pid, mode="reference"))
    # E2 aggregators
    for pid in ["iid", "dir0.3", "dir0.1"]:
        for a in AGGS:
            out["e2"].append(make(f"s2-e2-{pid}-{a}", "E2", tuned, partition=pid, aggregator=agg(a, tuned)))
    # E3 model scale (dir0.3)
    for model, rounds, ev in (("cnn-medium", 50, {"every": 1, "threads": 2}),
                              ("resnet18-gn", 20, {"every": 5, "threads": 4})):
        for a in ["fedavg", a_star]:
            out["e3"].append(make(f"s2-e3-{model}-{a}", "E3", tuned, model=model, rounds=rounds,
                                  aggregator=agg(a, tuned), eval=ev))
    # E4a payload sweep (echo, stage-1 data at the sites, no evaluation)
    pay = {"partition": None, "rounds": 3, "round_retries": 0, "deadline_s": 3600, "eval": {"every": 0},
           "retention": {"blobs": "rolling", "checkpoints": "latest"}, "strict_code_version": True}
    for scope, sites in [*FW_SITES.items(), ("mixed", BASE_SITES)]:
        for size in PAYLOAD_SIZES:
            out["e4a"].append(make(f"s2-e4a-{scope}-{size}mb", "E4", tuned, model=f"payload-{size}",
                                   sites=list(sites), **pay))
    # E4b bandwidth (payload-45, mixed)
    for profile in ["none", "wan1g", "wan100", "wan20"]:
        out["e4b"].append(make(f"s2-e4b-{profile}", "E4", tuned, model="payload-45", netem=profile, **pay))
    # E5 number of sites (iid, 20 rounds)
    out["e5"].append(make("s2-e5-6sites", "E5", tuned, partition="iid", rounds=20))
    out["e5"].append(make("s2-e5-12sites", "E5", tuned, partition="iid12", rounds=20,
                          sites=BASE_SITES + EXTRA_SITES))
    # E6 robustness (dir0.3, cnn-small, 20 rounds unless noted)
    e6 = {"rounds": 20}
    out["e6"].append(make("s2-e6a-strict", "E6", tuned, round_retries=3, **e6))
    out["e6"].append(make("s2-e6a-minsites", "E6", tuned, participation={"min_sites": 5}, deadline_s=120,
                          round_retries=2, **e6))
    out["e6"].append(make("s2-e6b-straggler", "E6", tuned, participation={"min_sites": 5}, deadline_s=20,
                          round_retries=2, **e6))
    out["e6"].append(make("s2-e6c-fedavg", "E6", tuned, participation={"fraction": 0.5}, rounds=50))
    out["e6"].append(make(f"s2-e6c-{a_star}", "E6", tuned, participation={"fraction": 0.5}, rounds=50,
                          aggregator=agg(a_star, tuned)))
    out["e6"].append(make("s2-e6d-resume", "E6", tuned, aggregator=agg(a_star, tuned), round_retries=2))
    out["e6"].append(make("s2-e6e-server-restart", "E6", tuned, round_retries=3, **e6))
    # E7 overhead vs native FedAvg (hub side; the native side runs deploy/central/<fw>/native/run.sh)
    for fw, sites in FW_SITES.items():
        out["e7"].append(make(f"s2-e7-{fw}-hub-cnn", "E7", tuned, partition="iid", rounds=20, sites=list(sites)))
        out["e7"].append(make(f"s2-e7-{fw}-hub-payload", "E7", tuned, model="payload-45", sites=list(sites),
                              **{**pay, "rounds": 5}))
    return out


def dump(cfg: dict) -> str:
    head = "# generated by scripts/stage2/make_configs.py; edit that script, not this file\n"
    return head + yaml.safe_dump(cfg, sort_keys=False, default_flow_style=None, width=110)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(REPO / "configs" / "stage2"))
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)
    out = Path(args.out)
    tuned_path = out / "tuned.yaml"
    tuned = {**DEFAULT_TUNED, **(yaml.safe_load(tuned_path.read_text()) if tuned_path.exists() else {})}
    changed = 0
    for group, items in configs(tuned).items():
        for name, cfg in items:
            path = out / group / f"{name}.yaml"
            text = dump(cfg)
            if path.exists() and path.read_text() == text:
                continue
            changed += 1
            if not args.check:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text)
    total = sum(len(v) for v in configs(tuned).values())
    print(f"{total} configs, {changed} {'would change' if args.check else 'written'} (tuned={tuned['tuned']})")
    return 1 if args.check and changed else 0


if __name__ == "__main__":
    sys.exit(main())

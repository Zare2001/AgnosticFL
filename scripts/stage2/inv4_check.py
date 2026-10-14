"""INV-4 check (PLAN.md stage 2 §2, §9.2): re-run a final config from the frozen SHA and compare.

    python scripts/stage2/inv4_check.py configs/stage2/e1/s2-e1-dir0.3-fedavg.yaml [--sim]

Runs the config again (real system, or hub.sim with --sim) under the name inv4-<name> in
~/flhub-runtime/runs/inv4/ (not uploaded), then compares every round's global_ref and
agg_state_ref with the original final run ~/flhub-runtime/runs/stage2/<name>-latest. Writes
docs/results/evidence/stage2/inv4-<name>.json. Exit 0 only if every round is identical.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from campaign import ACTIVE, PY, REPO, RUNS, bus, frozen_check, git, latest_run, utc  # noqa: E402

sys.path.insert(0, str(REPO))
from hub.orchestrator.checkpoint import RunDir  # noqa: E402
from hub.orchestrator.config import load_config  # noqa: E402

INV4 = RUNS.parent / "inv4"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("config")
    ap.add_argument("--sim", action="store_true", help="re-run in hub.sim instead of the real system")
    args = ap.parse_args(argv)
    cfg = load_config(args.config)
    ok, msg = frozen_check()
    print(msg)
    if not ok:
        return 2
    original = latest_run(cfg.name)
    if original is None:
        print(f"no original run {cfg.name}")
        return 2
    name = f"inv4-{cfg.name}"
    start = utc()
    if not args.sim:
        if ACTIVE.exists():
            print(f"another run is active: {ACTIVE.read_text()}")
            return 4
        ACTIVE.write_text(json.dumps({"name": name, "exp": cfg.experiment, "started": start}))
        bus("info", f"RUN start {name} {start}", f"INV-4 re-run of {cfg.name} from frozen SHA (not uploaded)")
    try:
        cmd = ([PY, "-m", "hub.sim", "--config", args.config, "--name", name, "--runs-dir", str(INV4), "--workers", "2"]
               if args.sim else
               [PY, "-m", "hub.orchestrator", "run", "--config", args.config, "--name", name, "--runs-dir", str(INV4)])
        rc = subprocess.run(cmd, cwd=REPO).returncode
    finally:
        if not args.sim:
            ACTIVE.unlink(missing_ok=True)
            bus("info", f"RUN end {name} {utc()}", "INV-4 re-run")
    rerun = latest_run(name, INV4)
    a, b = RunDir(original), RunDir(rerun)
    rounds = []
    for r in a.recorded_rounds():
        ra, rb = a.record(r), b.record(r) if b.record_path(r).exists() else {}
        rounds.append({"round": r, "global_equal": ra["global_ref"] == rb.get("global_ref"),
                       "state_equal": ra["agg_state_ref"] == rb.get("agg_state_ref")})
    res = {"check": "INV-4", "config": args.config, "original": str(a.path), "rerun": str(b.path),
           "mode": "sim" if args.sim else "real", "head": git("rev-parse", "HEAD"), "rc": rc,
           "all_equal": rc == 0 and all(x["global_equal"] and x["state_equal"] for x in rounds),
           "rounds": len(rounds) - 1, "per_round": rounds, "utc": utc()}
    out = REPO / "docs" / "results" / "evidence" / "stage2" / f"inv4-{cfg.name}.json"
    out.write_text(json.dumps(res, indent=1) + "\n")
    print(f"INV-4 {cfg.name}: all rounds identical = {res['all_equal']} ({out})")
    return 0 if res["all_equal"] else 1


if __name__ == "__main__":
    sys.exit(main())

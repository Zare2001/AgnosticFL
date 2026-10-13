"""E4a payload sweep for one scope with the stop rule (EXPERIMENTS.md E4a, PLAN.md stage 2 §7).

    python scripts/stage2/e4a_sweep.py --scope nvflare|vantage6|flower|mixed [--sizes 1,8,...,512] [--retry]

Per size, in order: purge the scope's framework stores (deploy/central/<fw>/purge.sh), then
run_final.py real (which applies the disk rule: free >= 3 GB + 4 x payload x sites). After the run:
the central RAM rule (any process peak RSS > 70 % of RAM ends the sweep for the scope), and on a
failed run the sweep stops and the framework agent is asked for a server-side knob; after the knob
is deployed, rerun with --retry --sizes <that size>,... (one retry; a second failure is the limit).
After a successful run its weight checkpoints are deleted (echo payloads: the global_ref hashes in
rounds/*.json suffice for INV-1, PLAN.md §7). Results go to <runs>/e4a-<scope>.json and the campaign log. While the sweep runs, HOLD_POST keeps
postproc.py from starting sims (CPU hygiene); the queued INV-1 jobs run after the sweep.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from campaign import PY, REPO, RUNS, bus, ensure_dirs, latest_run, record, utc  # noqa: E402

SIZES = [1, 8, 32, 64, 128, 256, 512]
FRAMEWORKS = ["nvflare", "vantage6", "flower"]
HOLD = RUNS / "HOLD_POST"


def total_ram_mb() -> float:
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemTotal:"):
            return float(line.split()[1]) / 1024
    return 0.0


def purge(fws: list[str]) -> dict:
    out = {}
    for fw in fws:
        script = REPO / "deploy" / "central" / fw / "purge.sh"
        if not script.exists():
            out[fw] = "no purge.sh"
            continue
        p = subprocess.run(["bash", str(script)], cwd=REPO, capture_output=True, text=True, timeout=900)
        out[fw] = f"rc={p.returncode} {(p.stdout + p.stderr).strip()[-300:]}"
    return out


def drop_checkpoints(run_dir: Path | None) -> float:
    """Payload runs: the per-round hashes in rounds/*.json are the INV-1 evidence; weights files go."""
    freed = 0
    for p in (run_dir / "rounds").glob("*.safetensors") if run_dir else []:
        freed += p.stat().st_size
        p.unlink()
    return round(freed / 2**20, 1)


def failure_text(run_dir: Path | None) -> str:
    if run_dir is None:
        return "no run dir"
    lines = []
    s = run_dir / "summary.json"
    if s.exists():
        lines.append(json.loads(s.read_text()).get("error", ""))
    e = run_dir / "events.jsonl"
    if e.exists():
        lines += [json.loads(x).get("error", "") for x in e.read_text().splitlines() if '"round_failed"' in x]
    return " | ".join(x for x in lines if x)[:1500]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scope", required=True, choices=FRAMEWORKS + ["mixed"])
    ap.add_argument("--sizes", default=",".join(map(str, SIZES)))
    ap.add_argument("--retry", action="store_true", help="this is the one retry after a server-side knob")
    args = ap.parse_args(argv)
    ensure_dirs()
    fws = FRAMEWORKS if args.scope == "mixed" else [args.scope]
    state_path = RUNS / f"e4a-{args.scope}.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {"scope": args.scope, "runs": []}
    ram = total_ram_mb()
    HOLD.write_text(f"e4a {args.scope} {utc()}")
    rc_all = 0
    try:
        for size in [int(x) for x in args.sizes.split(",")]:
            name = f"s2-e4a-{args.scope}-{size}mb"
            cfg = f"configs/stage2/e4a/{name}.yaml"
            purged = purge(fws)
            rc = subprocess.run([PY, "scripts/stage2/run_final.py", "real", cfg, "--notes",
                                 "E4a retry after knob" if args.retry else ""], cwd=REPO).returncode
            run_dir = latest_run(name)
            entry = {"size_mb": size, "name": name, "rc": rc, "retry": args.retry, "purge": purged, "utc": utc()}
            if rc == 3:
                entry["result"] = "testbed disk limit"
                state["runs"].append(entry)
                bus("info", f"S2 E4a {args.scope}: testbed disk limit at {size} MB", "sweep stopped for this scope")
                break
            peak = {}
            if run_dir and (run_dir / "sysmon" / "central.json").exists():
                procs = json.loads((run_dir / "sysmon" / "central.json").read_text()).get("procs", {})
                peak = {k: v["rss_mb_peak"] for k, v in procs.items()}
            entry["central_peak_rss_mb"] = peak
            over = {k: v for k, v in peak.items() if v > 0.7 * ram}
            if rc != 0:
                entry["result"] = "failed"
                entry["error"] = failure_text(run_dir)
                state["runs"].append(entry)
                rc_all = 1
                for fw in fws:
                    bus("request", f"S2 E4a {args.scope} FAILED at {size} MB{' (retry)' if args.retry else ''}: "
                        f"server-side knob?", f"{entry['error'][:1200]}\nrun dir {run_dir}. Per the stop rule: "
                        "propose a server-side knob (deploy-request + ADR) for ONE retry of this size, or confirm "
                        "this is the limit (component, error, knob, side).", to=fw)
                break
            entry["result"] = "ok"
            entry["checkpoints_freed_mb"] = drop_checkpoints(run_dir)
            state["runs"].append(entry)
            if over:
                entry["result"] = "ok, RAM rule hit"
                bus("info", f"S2 E4a {args.scope}: RAM rule at {size} MB (peak RSS > 70 % of RAM)", json.dumps(over))
                break
            state_path.write_text(json.dumps(state, indent=1))
    finally:
        state_path.write_text(json.dumps(state, indent=1))
        HOLD.unlink(missing_ok=True)
    record({"event": "e4a-sweep", "scope": args.scope, "last": state["runs"][-1] if state["runs"] else None})
    return rc_all


if __name__ == "__main__":
    sys.exit(main())

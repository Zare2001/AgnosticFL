"""Run one stage-2 final run on central (PLAN.md stage 2 §6) or queue its post-processing.

    python scripts/stage2/run_final.py real <config.yaml> [--replay] [--equal-to RUN_NAME] [--notes ".."]
                                       [--no-post] [--allow-unfrozen]
    python scripts/stage2/run_final.py sim <config.yaml> --seed 2027        (queued; postproc.py runs it)
    python scripts/stage2/run_final.py reference <config.yaml> --kind centralized|local   (queued)
    python scripts/stage2/run_final.py native <fw> <config.yaml> --hub-run RUN_NAME --name N   (runs now)

real: frozen-SHA and disk checks -> bus "RUN start <name> <UTC>" -> hub.orchestrator -> bus "RUN end"
(with per-round UTC windows) -> central sysmon summary into <run_dir>/sysmon/central.json -> queue
INV-1 + finalize for scripts/stage2/postproc.py. ACTIVE_RUN marks the run for the CPU-hygiene rule.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from campaign import (  # noqa: E402
    ACTIVE,
    LOGS,
    PY,
    REPO,
    RUNS,
    bus,
    enqueue,
    ensure_dirs,
    free_gb,
    frozen_check,
    latest_run,
    record,
    utc,
)

sys.path.insert(0, str(REPO))
from hub.orchestrator.config import load_config  # noqa: E402


def payload_disk_gb(cfg) -> float:
    from flhub_core.model import is_payload, payload_mb

    if not is_payload(cfg.model):
        return 0.0
    return 4 * payload_mb(cfg.model) / 1024 * len(cfg.sites)


def round_windows(run_dir: Path) -> list[str]:
    out = []
    for p in sorted((run_dir / "rounds").glob("r[0-9][0-9][0-9][0-9].json")):
        rec = json.loads(p.read_text())
        if rec.get("round", 0) > 0:
            out.append(f"r{rec['round']}: {rec.get('utc_start')} .. {rec.get('utc_end')}")
    return out


def sysmon_central(run_dir: Path, start: str, end: str, name: str) -> None:
    out = run_dir / "sysmon"
    out.mkdir(parents=True, exist_ok=True)
    subprocess.run(["python3", str(REPO / "scripts" / "sysmon.py"), "summarize", "--from", start, "--to", end,
                    "--run", name, "-o", str(out / "central.json")], capture_output=True, timeout=300)


def wait_pause() -> None:
    """Operator hold between runs (e.g. a store purge): wait while <runs>/PAUSE exists."""
    pause = RUNS / "PAUSE"
    if pause.exists():
        print(f"PAUSE present ({pause.read_text().strip()}); waiting", flush=True)
        while pause.exists():
            time.sleep(5)


def real(args) -> int:
    wait_pause()
    cfg = load_config(args.config)
    ok, msg = frozen_check()
    print(msg)
    if not ok and not args.allow_unfrozen:
        print("refusing: code is not the frozen SHA")
        return 2
    need = 3.0 + payload_disk_gb(cfg)
    proj = RUNS / "disk_projection.json"  # measured peak use of a similar run beats the rule of thumb
    if proj.exists():
        need = max(need, 3.0 + float(json.loads(proj.read_text()).get(cfg.name, 0.0)))
    if free_gb() < need:
        bus("info", f"S2 testbed disk limit: {cfg.name} needs {need:.1f} GB free, have {free_gb():.1f} GB",
            "Run not started (PLAN.md §7 disk rule).")
        record({"event": "disk_limit", "name": cfg.name, "need_gb": need, "free_gb": free_gb()})
        print(f"testbed disk limit: need {need:.1f} GB, free {free_gb():.1f} GB")
        return 3
    ensure_dirs()
    if ACTIVE.exists():
        print(f"another run is active: {ACTIVE.read_text()}")
        return 4
    start = utc()
    ACTIVE.write_text(json.dumps({"name": cfg.name, "exp": cfg.experiment, "started": start}))
    bus("info", f"RUN start {cfg.name} {start}", f"config {args.config}; sites {' '.join(cfg.sites)}; "
        f"model {cfg.model}; agg {cfg.aggregator['name']}; rounds {cfg.rounds}; netem {cfg.netem}")
    log = LOGS / f"{cfg.name}.log"
    t0 = time.time()
    try:
        with open(log, "a") as f:
            rc = subprocess.run([PY, "-m", "hub.orchestrator", "run", "--config", args.config], cwd=REPO,
                                stdout=f, stderr=subprocess.STDOUT).returncode
    finally:
        ACTIVE.unlink(missing_ok=True)
    end = utc()
    run_dir = latest_run(cfg.name)
    summary = json.loads((run_dir / "summary.json").read_text()) if run_dir and (run_dir / "summary.json").exists() \
        else {"status": "no run dir"}
    windows = round_windows(run_dir) if run_dir else []
    body = (f"status {summary.get('status')} rc={rc} rounds={summary.get('rounds')} "
            f"median_round_s={summary.get('round_time_median_s')} run_dir={run_dir}\n"
            + ("\n".join(windows) if len(windows) <= 12 else f"{windows[0]}\n...\n{windows[-1]}"))
    bus("info", f"RUN end {cfg.name} {end}", body)
    if run_dir:
        sysmon_central(run_dir, start, end, cfg.name)
    record({"event": "run", "name": cfg.name, "exp": cfg.experiment, "start": start, "end": end, "rc": rc,
            "status": summary.get("status"), "run_dir": str(run_dir), "seconds": round(time.time() - t0)})
    print(f"{cfg.name}: rc={rc} status={summary.get('status')} run_dir={run_dir} ({time.time() - t0:.0f} s)")
    if rc == 0 and summary.get("status") == "finished" and not args.no_post:
        enqueue({"type": "real-post", "name": cfg.name, "exp": cfg.experiment, "config": args.config,
                 "run_dir": str(run_dir), "replay": args.replay, "equal_to": args.equal_to, "notes": args.notes})
    return rc


def native(args) -> int:
    """E7: the framework agent's native FedAvg script, run like a real run; finalize is queued."""
    wait_pause()
    cfg = load_config(args.config)
    ok, msg = frozen_check()
    print(msg)
    if not ok and not args.allow_unfrozen:
        return 2
    script = REPO / "deploy" / "central" / args.fw / "native" / "run.sh"
    if not script.exists():
        print(f"missing {script}")
        return 2
    ensure_dirs()
    if ACTIVE.exists():
        print(f"another run is active: {ACTIVE.read_text()}")
        return 4
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    out = RUNS / f"{args.name}-{stamp}"  # created by the native script (NVFlare's refuses an existing dir)
    link = RUNS / f"{args.name}-latest"
    link.unlink(missing_ok=True)
    link.symlink_to(out.name)
    start = utc()
    ACTIVE.write_text(json.dumps({"name": args.name, "exp": cfg.experiment, "started": start}))
    bus("info", f"RUN start {args.name} {start}", f"native {args.fw}: {script} {args.config} {out}")
    t0 = time.time()
    try:
        with open(LOGS / f"{args.name}.log", "a") as f:
            rc = subprocess.run(["bash", str(script), args.config, str(out)], cwd=REPO, stdout=f,
                                stderr=subprocess.STDOUT).returncode
    finally:
        ACTIVE.unlink(missing_ok=True)
    end = utc()
    bus("info", f"RUN end {args.name} {end}", f"native {args.fw} rc={rc} out={out}")
    sysmon_central(out, start, end, args.name)
    record({"event": "native", "name": args.name, "fw": args.fw, "start": start, "end": end, "rc": rc,
            "run_dir": str(out), "seconds": round(time.time() - t0)})
    if rc == 0:
        enqueue({"type": "native", "name": args.name, "exp": cfg.experiment, "run_dir": str(out),
                 "hub_run": args.hub_run, "notes": args.notes})
    print(f"{args.name}: rc={rc} out={out} ({time.time() - t0:.0f} s)")
    return rc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="run_final.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("real")
    r.add_argument("config")
    r.add_argument("--replay", action="store_true")
    r.add_argument("--equal-to")
    r.add_argument("--notes", default="")
    r.add_argument("--no-post", action="store_true")
    r.add_argument("--allow-unfrozen", action="store_true")
    s = sub.add_parser("sim")
    s.add_argument("config")
    s.add_argument("--seed", type=int, required=True)
    s.add_argument("--notes", default="")
    f = sub.add_parser("reference")
    f.add_argument("config")
    f.add_argument("--kind", choices=["centralized", "local"], required=True)
    f.add_argument("--notes", default="")
    n = sub.add_parser("native")
    n.add_argument("fw", choices=["nvflare", "vantage6", "flower"])
    n.add_argument("config")
    n.add_argument("--hub-run", required=True, help="name of the matching hub run")
    n.add_argument("--name", required=True)
    n.add_argument("--notes", default="")
    n.add_argument("--allow-unfrozen", action="store_true")
    args = ap.parse_args(argv)
    if args.cmd == "real":
        return real(args)
    if args.cmd == "native":
        return native(args)
    cfg = load_config(args.config)
    if args.cmd == "sim":
        name = f"{cfg.name}-seed{args.seed}"
        enqueue({"type": "sim", "name": name, "exp": cfg.experiment, "config": args.config, "seed": args.seed,
                 "counterpart": cfg.name, "notes": args.notes})
    else:
        enqueue({"type": "reference", "name": cfg.name, "exp": cfg.experiment, "config": args.config,
                 "kind": args.kind, "notes": args.notes})
    print(f"queued {args.cmd} job for {cfg.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

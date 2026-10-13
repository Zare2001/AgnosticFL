"""Central-side fault injection for E6 (EXPERIMENTS.md E6d, E6e; PLAN.md stage 2 §8.5).

    python scripts/stage2/inject_central.py e6d [--config configs/stage2/e6/s2-e6d-resume.yaml] [--round 20]
           [--delay 3] [--equal-to s2-e2-dir0.3-fedavgm]
    python scripts/stage2/inject_central.py e6e --run s2-e6e-server-restart
           [--plan after:3:nvflare,mid:6:nvflare,after:9:vantage6,mid:12:vantage6,after:15:flower,mid:18:flower]
           [--mid-delay 3]

e6d runs the whole E6d run: RUN start, orchestrator, at round N (+delay s, the next round in flight)
SIGKILL of the orchestrator and the three adapter processes, adapter restart (ADAPTER_START below),
`--resume`, RUN end, then queues the post-processing with --equal-to (bit-exact final weights).
e6e runs next to `run_final.py real configs/stage2/e6/s2-e6e-server-restart.yaml`: `after:N:fw` holds
the orchestrator after round N (hold-after-rNNNN file), restarts that framework server and releases
the hold once it healed; `mid:N:fw` restarts it while round N is in flight. Every injection is
announced on the bus first and logged to <run_dir>/injections.jsonl with UTC times.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from campaign import ACTIVE, LOGS, PY, REPO, RUNS, bus, enqueue, ensure_dirs, record, utc  # noqa: E402

ADAPTER_PATTERNS = {"nvflare": "adapters.nvflare.service", "vantage6": "-m adapters.vantage6",
                    "flower": "adapters.flower.service"}
# command that starts only the adapter again (framework server and hospitals untouched); bus #156,
# answers #160 (flower) and #162 (nvflare: start.sh starts only what is not running)
ADAPTER_START = {
    "nvflare": "bash deploy/central/nvflare/start.sh",
    "vantage6": "bash deploy/central/vantage6/start_adapter.sh",
    "flower": "bash deploy/central/flower/restart.sh adapter",
}
SERVER_RESTART = {fw: f"bash deploy/central/{fw}/restart_server.sh" for fw in ("nvflare", "vantage6", "flower")}


def pids(pattern: str) -> list[int]:
    out = []
    for entry in os.scandir("/proc"):
        if not entry.name.isdigit() or int(entry.name) == os.getpid():
            continue
        try:
            cmd = Path(f"/proc/{entry.name}/cmdline").read_bytes().replace(b"\0", b" ").decode()
        except OSError:
            continue
        if pattern in cmd and "inject_central" not in cmd:
            out.append(int(entry.name))
    return out


def log_injection(run_dir: Path, **fields) -> None:
    with open(run_dir / "injections.jsonl", "a") as f:
        f.write(json.dumps({"utc": utc(ms=True), **fields}) + "\n")


def wait_for(path: Path, timeout: float = 7200) -> None:
    end = time.time() + timeout
    while not path.exists():
        if time.time() > end:
            raise TimeoutError(f"{path} did not appear")
        time.sleep(0.2)


def run_dir_of(name: str, timeout: float = 600) -> Path:
    link = RUNS / f"{name}-latest"
    end = time.time() + timeout
    while time.time() < end:
        if link.exists() and (link.resolve() / "rounds" / "r0000.json").exists():
            return link.resolve()
        time.sleep(0.5)
    raise TimeoutError(f"run dir of {name} did not appear")


def e6d(args) -> int:
    from hub.orchestrator.config import load_config

    cfg = load_config(args.config)
    ensure_dirs()
    start = utc()
    ACTIVE.write_text(json.dumps({"name": cfg.name, "exp": cfg.experiment, "started": start}))
    bus("info", f"RUN start {cfg.name} {start}", f"E6d: SIGKILL orchestrator + adapters after round {args.round}, "
        f"then --resume; config {args.config}")
    log = open(LOGS / f"{cfg.name}.log", "a")
    orch = subprocess.Popen([PY, "-m", "hub.orchestrator", "run", "--config", args.config], cwd=REPO, stdout=log,
                            stderr=subprocess.STDOUT)
    try:
        run_dir = run_dir_of(cfg.name)
        wait_for(run_dir / "rounds" / f"r{args.round:04d}.json")
        time.sleep(args.delay)
        victims = {"orchestrator": [orch.pid]} | {fw: pids(p) for fw, p in ADAPTER_PATTERNS.items()}
        bus("info", f"E6 inject SIGKILL orchestrator+3 adapters at {utc()}", json.dumps(victims))
        t_kill = time.time()
        for group in victims.values():
            for pid in group:
                os.kill(pid, signal.SIGKILL)
        orch.wait()
        log_injection(run_dir, action="sigkill", targets=victims, after_round=args.round)
        for fw, cmd in ADAPTER_START.items():
            rc = subprocess.run(["bash", "-c", cmd], cwd=REPO, stdout=log, stderr=subprocess.STDOUT).returncode
            log_injection(run_dir, action="adapter_start", framework=fw, rc=rc, after_s=round(time.time() - t_kill, 1))
        log_injection(run_dir, action="resume", after_s=round(time.time() - t_kill, 1))
        rc = subprocess.run([PY, "-m", "hub.orchestrator", "run", "--resume", str(run_dir)], cwd=REPO, stdout=log,
                            stderr=subprocess.STDOUT).returncode
    finally:
        ACTIVE.unlink(missing_ok=True)
        log.close()
    end = utc()
    summary = json.loads((run_dir / "summary.json").read_text())
    bus("info", f"RUN end {cfg.name} {end}", f"status {summary.get('status')} rc={rc} resumed after round "
        f"{args.round}; run_dir={run_dir}")
    record({"event": "run", "name": cfg.name, "exp": cfg.experiment, "start": start, "end": end, "rc": rc,
            "status": summary.get("status"), "run_dir": str(run_dir), "injection": "e6d"})
    if rc == 0 and summary.get("status") == "finished":
        enqueue({"type": "real-post", "name": cfg.name, "exp": cfg.experiment, "config": args.config,
                 "run_dir": str(run_dir), "replay": False, "equal_to": args.equal_to,
                 "notes": f"SIGKILL hub + adapters after r{args.round}, resumed"})
    return rc


def e6e(args) -> int:
    run_dir = run_dir_of(args.run)
    plan = [(k, int(n), fw) for k, n, fw in (p.split(":") for p in args.plan.split(","))]
    for kind, n, _ in plan:
        if kind == "after":
            (run_dir / f"hold-after-r{n:04d}").touch()
    for kind, n, fw in plan:
        if kind == "after":
            wait_for(run_dir / "rounds" / f"r{n:04d}.json")
        else:
            wait_for(run_dir / "rounds" / f"r{n - 1:04d}.json")
            time.sleep(args.mid_delay)
        bus("info", f"E6 inject restart {fw} server ({kind} round {n}) at {utc()}", SERVER_RESTART[fw])
        t0 = time.time()
        log_injection(run_dir, action="server_restart_begin", framework=fw, kind=kind, round=n)
        rc = subprocess.run(["bash", "-c", SERVER_RESTART[fw]], cwd=REPO, capture_output=True, text=True)
        log_injection(run_dir, action="server_restart_end", framework=fw, kind=kind, round=n, rc=rc.returncode,
                      seconds=round(time.time() - t0, 1), output=(rc.stdout + rc.stderr)[-1500:])
        if kind == "after":
            (run_dir / f"hold-after-r{n:04d}").unlink(missing_ok=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="inject_central.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("e6d")
    d.add_argument("--config", default="configs/stage2/e6/s2-e6d-resume.yaml")
    d.add_argument("--round", type=int, default=20)
    d.add_argument("--delay", type=float, default=3.0)
    d.add_argument("--equal-to", default="s2-e2-dir0.3-fedavgm")
    e = sub.add_parser("e6e")
    e.add_argument("--run", default="s2-e6e-server-restart")
    e.add_argument("--plan", default="after:3:nvflare,mid:6:nvflare,after:9:vantage6,mid:12:vantage6,"
                                     "after:15:flower,mid:18:flower")
    e.add_argument("--mid-delay", type=float, default=3.0)
    args = ap.parse_args(argv)
    return e6d(args) if args.cmd == "e6d" else e6e(args)


if __name__ == "__main__":
    sys.exit(main())

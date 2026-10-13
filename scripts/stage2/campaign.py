"""Shared paths and helpers for the stage-2 campaign scripts (run_final.py, postproc.py)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PY = sys.executable
RUNS = Path(os.environ.get("FLHUB_STAGE2_RUNS", str(Path.home() / "flhub-runtime" / "runs" / "stage2")))
QUEUE, DONE, FAILED = RUNS / "queue", RUNS / "queue-done", RUNS / "queue-failed"
LOGS = RUNS / "logs"
ACTIVE = RUNS / "ACTIVE_RUN"
CAMPAIGN = RUNS / "campaign.jsonl"
FROZEN = Path.home() / "flhub-runtime" / "frozen_sha"
# what a final run executes (PLAN.md §5); report/finalize tooling in scripts/ may still be fixed
FROZEN_PATHS = ["flhub_core", "adapters", "clients", "hub", "configs/stage2", "deploy/central"]
NO_SIM_EXPERIMENTS = {"E3", "E4", "E5", "E7"}  # PLAN.md §6 CPU hygiene


def utc(ms: bool = False) -> str:
    fmt = "%Y-%m-%dT%H:%M:%S.%f" if ms else "%Y-%m-%dT%H:%M:%S"
    s = datetime.now(timezone.utc).strftime(fmt)
    return (s[:-3] if ms else s) + "Z"


def ensure_dirs() -> None:
    for d in (RUNS, QUEUE, DONE, FAILED, LOGS):
        d.mkdir(parents=True, exist_ok=True)


def bus(kind: str, subject: str, body: str = "", to: str = "all") -> None:
    try:
        subprocess.run([str(REPO / "tools" / "bus"), "send", "--to", to, "--type", kind, "--subject", subject,
                        "--body", body], cwd=REPO, capture_output=True, timeout=60)
    except Exception as exc:  # the bus must never stop a run
        print(f"bus send failed: {exc}", file=sys.stderr)


def git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True).stdout.strip()


def frozen_check() -> tuple[bool, str]:
    """(ok, message): HEAD must not differ from the frozen SHA in the frozen paths, nothing uncommitted."""
    dirty = git("status", "--porcelain", "-uno", "--", *FROZEN_PATHS)
    if dirty:
        return False, f"uncommitted changes in frozen paths:\n{dirty}"
    if not FROZEN.exists():
        return True, f"no frozen SHA yet (pre-S3-frozen); HEAD {git('rev-parse', '--short', 'HEAD')}"
    sha = FROZEN.read_text().split()[0]
    diff = git("diff", "--stat", sha, "HEAD", "--", *FROZEN_PATHS)
    if diff:
        return False, f"HEAD differs from frozen {sha} in frozen paths:\n{diff}"
    return True, f"frozen {sha[:10]} == HEAD {git('rev-parse', '--short', 'HEAD')} in frozen paths"


def free_gb(path: Path = Path.home()) -> float:
    return shutil.disk_usage(path).free / 2**30


def latest_run(name: str, root: Path = RUNS) -> Path | None:
    p = root / f"{name}-latest"
    return p.resolve() if p.exists() else None


def record(event: dict) -> None:
    ensure_dirs()
    with open(CAMPAIGN, "a") as f:
        f.write(json.dumps({"utc": utc(), **event}) + "\n")


def enqueue(job: dict) -> Path:
    ensure_dirs()
    path = QUEUE / f"{time.strftime('%Y%m%dT%H%M%S')}-{time.time_ns() % 10**6:06d}-{job['name']}.json"
    path.write_text(json.dumps(job, indent=1))
    return path


def finalized(name: str) -> bool:
    rows = (REPO / "docs" / "report" / "stage2" / "runs.md").read_text().splitlines()
    return any(r.startswith(f"| {name} |") for r in rows)

"""Post-processing worker for the stage-2 campaign: INV-1 sims, seed sims, references, finalize.

    nohup python scripts/stage2/postproc.py [--once] > ~/flhub-runtime/runs/stage2/logs/postproc.log 2>&1 &

Takes jobs from ~/flhub-runtime/runs/stage2/queue/ in order (written by run_final.py):
  real-post  INV-1 (hub.sim --compare, or --replay, or --equal-to) then finalize_run.py --mode real
  sim        hub.sim --seed (extra seed) then finalize --mode sim --counterpart (deferred until the
             counterpart real run is in runs.md)
  reference  hub.sim --mode centralized|local then finalize --mode reference
  native     finalize --mode native --hub-run (deferred until the hub run is finalized)
CPU hygiene (PLAN.md §6): no sims while an E3/E4/E5/E7 run is active or HOLD_POST exists (a sweep
in progress); otherwise nice 19 and at most 2 workers while any run is active (4 when idle).
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from campaign import (  # noqa: E402
    ACTIVE,
    DONE,
    FAILED,
    LOGS,
    NO_SIM_EXPERIMENTS,
    PY,
    QUEUE,
    REPO,
    RUNS,
    bus,
    ensure_dirs,
    finalized,
    latest_run,
    record,
)


def active() -> dict | None:
    try:
        return json.loads(ACTIVE.read_text())
    except (OSError, ValueError):
        return None


def sh(cmd: list[str], log: Path) -> int:
    with open(log, "a") as f:
        f.write(f"\n$ {' '.join(cmd)}\n")
        f.flush()
        return subprocess.run(cmd, cwd=REPO, stdout=f, stderr=subprocess.STDOUT).returncode


def sim_cmd(workers: int) -> list[str]:
    return ["nice", "-n", "19", PY, "-m", "hub.sim", "--workers", str(workers)]


def finalize(args: list[str], log: Path) -> int:
    return sh([PY, "scripts/stage2/finalize_run.py", *args], log)


def process(job: dict, workers: int) -> str:
    """'done', 'defer' or 'failed'."""
    name, kind = job["name"], job["type"]
    log = LOGS / f"{name}.post.log"
    notes = ["--notes", job.get("notes", "")] if job.get("notes") else []
    if job.get("exp") == "E7" and kind == "real-post":
        notes += ["--no-retention"]  # the native-vs-hub comparison needs every round of the E7 hub runs
    if kind == "real-post":
        run_dir = job["run_dir"]
        if job.get("equal_to"):
            other = latest_run(job["equal_to"])
            if other is None:
                return "defer"
            rc = finalize([run_dir, "--mode", "real", "--equal-to", str(other), *notes], log)
        elif job.get("replay"):
            rc = sh(sim_cmd(workers) + ["--replay", run_dir, "--rounds", "1,2,3,last"], log)
            rc = rc or finalize([run_dir, "--mode", "real", "--replay", *notes], log)
        else:
            rc = sh(sim_cmd(workers) + ["--config", job["config"], "--compare", run_dir, "--name",
                                        f"{name}-inv1sim", "--runs-dir", str(RUNS / "inv1")], log)
            if rc == 0 and ("payload" in name or "-e4" in name):  # echo payloads: hashes suffice (PLAN §7)
                sim_dir = latest_run(f"{name}-inv1sim", RUNS / "inv1")
                for p in (sim_dir / "rounds").glob("*.safetensors") if sim_dir else []:
                    p.unlink()
            rc = rc or finalize([run_dir, "--mode", "real", *notes], log)
            if rc == 0:  # finalized: the comparison is recorded, the sim's checkpoints are not needed (PLAN §7)
                sim_dir = latest_run(f"{name}-inv1sim", RUNS / "inv1")
                for p in (sim_dir / "rounds").glob("*.safetensors") if sim_dir else []:
                    p.unlink()
    elif kind == "sim":
        if not finalized(job["counterpart"]):
            return "defer"
        rc = sh(sim_cmd(workers) + ["--config", job["config"], "--seed", str(job["seed"]), "--name", name], log)
        run_dir = latest_run(name)
        rc = rc or finalize([str(run_dir), "--mode", "sim", "--counterpart", job["counterpart"], *notes], log)
    elif kind == "reference":
        rc = sh(sim_cmd(workers) + ["--config", job["config"], "--mode", job["kind"], "--name", name], log)
        run_dir = latest_run(name)
        rc = rc or finalize([str(run_dir), "--mode", "reference", *notes], log)
    elif kind == "native":
        if not finalized(job["hub_run"]):
            return "defer"
        rc = finalize([job["run_dir"], "--mode", "native", "--hub-run", str(latest_run(job["hub_run"])),
                       "--name", name, "--exp", job["exp"], *notes], log)
    else:
        rc = 99
    return "done" if rc == 0 else "failed"


def loop(once: bool) -> None:
    ensure_dirs()
    while True:
        jobs = sorted(QUEUE.glob("*.json"))
        progressed = False
        for path in jobs:
            run = active()
            if (run and run.get("exp") in NO_SIM_EXPERIMENTS) or (RUNS / "HOLD_POST").exists():
                break
            workers = 2 if run else 4
            job = json.loads(path.read_text())
            t0 = time.time()
            status = process(job, workers)
            if status == "defer":
                continue
            progressed = True
            dest = DONE if status == "done" else FAILED
            shutil.move(str(path), dest / path.name)
            record({"event": f"post-{status}", "name": job["name"], "type": job["type"],
                    "seconds": round(time.time() - t0)})
            if status == "failed":
                bus("info", f"S2 post-processing FAILED for {job['name']} ({job['type']})",
                    f"log: {LOGS / (job['name'] + '.post.log')} on central; files kept, nothing uploaded",
                    to="central")
            break  # re-read the queue and the ACTIVE marker after every job
        if once and not progressed:
            return
        if not progressed:
            time.sleep(15)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="exit when nothing is left to do")
    args = ap.parse_args(argv)
    loop(args.once)
    return 0


if __name__ == "__main__":
    sys.exit(main())

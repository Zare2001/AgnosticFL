"""E5 number of sites (EXPERIMENTS.md E5): 6 base sites, then 12 with the extra sites up.

    python scripts/stage2/e5_run.py [--skip-6]

Holds post-processing for the phase (no sims during E5). Runs s2-e5-6sites; asks every framework agent
'E5 EXTRAS UP' and waits for 'E5 EXTRAS ONLINE'; runs s2-e5-12sites (the orchestrator also waits until
all 12 sites are online); then 'E5 EXTRAS DOWN' and waits for 'E5 EXTRAS STOPPED'.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from campaign import PY, REPO, RUNS, bus, utc  # noqa: E402
from e4b_run import send_all, wait_ack  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-6", action="store_true")
    args = ap.parse_args(argv)
    hold = RUNS / "HOLD_POST"
    hold.write_text(f"E5 {utc()}")
    rc = 0
    try:
        if not args.skip_6:
            rc |= subprocess.run([PY, "scripts/stage2/run_final.py", "real", "configs/stage2/e5/s2-e5-6sites.yaml"],
                                 cwd=REPO).returncode
        first = send_all("E5 EXTRAS UP", "Please start your two E5 extra sites now and reply 'E5 EXTRAS ONLINE' "
                                         "once they show online in your status.")
        if not wait_ack(first, "E5 EXTRAS ONLINE", timeout=1800):
            return 3
        rc |= subprocess.run([PY, "scripts/stage2/run_final.py", "real", "configs/stage2/e5/s2-e5-12sites.yaml"],
                             cwd=REPO).returncode
    finally:
        first = send_all("E5 EXTRAS DOWN", "E5 done: please stop your two extra sites (and their watchdog entries) "
                                           "and reply 'E5 EXTRAS STOPPED'.")
        wait_ack(first, "E5 EXTRAS STOPPED", timeout=1800)
        hold.unlink(missing_ok=True)
        bus("info", f"S4 E5 step done rc={rc} {utc()}")
    return rc


if __name__ == "__main__":
    sys.exit(main())

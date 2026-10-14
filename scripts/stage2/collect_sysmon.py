"""Fetch the framework agents' sysmon summaries from the bus into the run dirs (PLAN.md §4.3).

    python scripts/stage2/collect_sysmon.py [--since 98]

Every bus file named after a final run (s2-e1-iid-fedavg-nvflare-sysmon.json,
sysmon-flower-s2-e1-iid-fedavg.json) is stored as <run_dir>/sysmon/<file name>, unless it is there already.
Files of other runs that contain a final run's name (inv4-<run>, dry runs) are skipped.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from campaign import REPO, RUNS  # noqa: E402

FWS = ("nvflare", "vantage6", "flower")
FILE_MSG = re.compile(r"^#(\d+) \S+ \[\w+\] (\w+) -> \S+: file #(\d+): (\S+)")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", type=int, default=98)
    args = ap.parse_args(argv)
    log = subprocess.run([str(REPO / "tools" / "bus"), "log", "--since", str(args.since), "--limit", "5000"],
                         cwd=REPO, capture_output=True, text=True).stdout
    names = sorted((p.name[: -len("-latest")] for p in RUNS.glob("*-latest")), key=len, reverse=True)
    got = 0
    for line in reversed(log.splitlines()):  # newest first: a re-run's summary wins over the failed attempt's
        m = FILE_MSG.match(line)
        if not m or not m.group(4).endswith(".json"):
            continue
        file_id, fname = m.group(3), m.group(4)
        stem = fname[:-len(".json")]
        # exact names only: sysmon-<fw>-<run>.json or <run>-<fw>-sysmon.json (not inv4-<run>, dry runs)
        run = next((n for n in names for fw in FWS if stem in (f"sysmon-{fw}-{n}", f"{n}-{fw}-sysmon")), None)
        if run is None:
            continue
        dest = (RUNS / f"{run}-latest").resolve() / "sysmon" / fname
        if dest.exists():
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([str(REPO / "tools" / "bus"), "get", file_id, "-o", str(dest)], cwd=REPO, check=True,
                       capture_output=True)
        got += 1
        print(f"{run}: {fname} (bus file #{file_id}, from {m.group(2)})")
    print(f"{got} new sysmon summaries")
    return 0


if __name__ == "__main__":
    sys.exit(main())

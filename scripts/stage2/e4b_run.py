"""One E4b run with network shaping on both ends (EXPERIMENTS.md E4b, PLAN.md stage 2 §8).

    python scripts/stage2/e4b_run.py <profile>          profile: none | wan1g | wan100 | wan20

For a shaped profile: central applies deploy/central/netem.sh to all three framework VMs in one call
(revert timer first), asks every framework agent with 'NETEM APPLY <profile> <minutes>' and waits for
replies containing 'NETEM APPLIED <profile>'; then runs s2-e4b-<profile> (run_final.py, HOLD_POST
for the whole step); then clears central, asks 'NETEM CLEAR <profile>' and waits for 'NETEM CLEARED'.
Central's rule is cleared even when the run fails.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from campaign import PY, REPO, RUNS, bus, utc  # noqa: E402

PEERS = ["145.38.189.142", "145.38.186.63", "145.38.187.20"]
AGENTS = ["nvflare", "vantage6", "flower"]
MINUTES = {"wan1g": 30, "wan100": 40, "wan20": 75}
MSG = re.compile(r"^#(\d+) \S+ \[[\w-]+\] (\S+) -> \S+: (.*)$")


def send_all(subject: str, body: str) -> int:
    first = None
    for a in AGENTS:
        out = subprocess.run([str(REPO / "tools" / "bus"), "send", "--to", a, "--type", "request", "--subject",
                              subject, "--body", body], cwd=REPO, capture_output=True, text=True).stdout
        m = re.search(r"sent #(\d+)", out)
        first = int(m.group(1)) if m and first is None else first
    return first or 0


def wait_ack(since: int, keyword: str, timeout: float = 900) -> bool:
    waiting = set(AGENTS)
    end = time.time() + timeout
    while waiting and time.time() < end:
        log = subprocess.run([str(REPO / "tools" / "bus"), "log", "--since", str(since), "--limit", "1000"], cwd=REPO,
                             capture_output=True, text=True).stdout
        for line in log.splitlines():
            m = MSG.match(line)
            if m and m.group(2) in waiting and keyword.upper() in m.group(3).upper():
                print(f"{keyword} <- {m.group(2)} (#{m.group(1)})", flush=True)
                waiting.discard(m.group(2))
        if waiting:
            time.sleep(10)
    if waiting:
        print(f"timeout waiting for {keyword} from {sorted(waiting)}")
    return not waiting


def netem(*args: str) -> int:
    p = subprocess.run(["bash", str(REPO / "deploy" / "central" / "netem.sh"), *args], cwd=REPO,
                       capture_output=True, text=True)
    print((p.stdout + p.stderr)[-1500:], flush=True)
    return p.returncode


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("profile", choices=["none", "wan1g", "wan100", "wan20"])
    args = ap.parse_args(argv)
    prof = args.profile
    cfg = f"configs/stage2/e4b/s2-e4b-{prof}.yaml"
    hold = RUNS / "HOLD_POST"
    hold.write_text(f"E4b {prof} {utc()}")
    rc = 1
    try:
        if prof != "none":
            minutes = MINUTES[prof]
            if netem("apply", prof, *PEERS, "--minutes", str(minutes)) != 0:
                print("central netem failed; not running")
                return 2
            first = send_all(f"NETEM APPLY {prof} {minutes}",
                             f"E4b: apply your netem.sh profile {prof} now (revert timer {minutes} min first), then "
                             f"reply 'NETEM APPLIED {prof}'. Central already shapes its egress to all three VMs.")
            if not wait_ack(first, f"NETEM APPLIED {prof}"):
                return 3
        rc = subprocess.run([PY, "scripts/stage2/run_final.py", "real", cfg], cwd=REPO).returncode
    finally:
        if prof != "none":
            netem("clear")
            first = send_all(f"NETEM CLEAR {prof}", "E4b run done: clear your netem rule and revert timer, verify "
                                                    "'tc qdisc show' = default, reply 'NETEM CLEARED'.")
            wait_ack(first, "NETEM CLEARED")
            netem("show")
        hold.unlink(missing_ok=True)
        bus("info", f"S4 E4b {prof} step done rc={rc} {utc()}", "central netem cleared" if prof != "none" else "")
    return rc


if __name__ == "__main__":
    sys.exit(main())

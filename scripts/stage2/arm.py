"""E6 ARM handshake (PLAN.md stage 2 §6): send ARM requests, wait for every ARMED reply.

    python scripts/stage2/arm.py --run s2-e6a-strict \
        --arm "nvflare:nvf-h1 kill at round 5 +0.5s" --arm "vantage6:v6-h3 kill at round 10 +0.3s" \
        --arm "flower:flw-h5 kill-node at round 15 +4s" [--timeout 1200]

Each --arm "<agent>:<site> <action ...>" becomes a bus request "ARM <run> <site> <action ...>" to that
agent. Exit 0 once every addressed agent sent a message mentioning ARMED and the run name after
the requests; exit 1 on timeout (then the run must not start).
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BUS = str(REPO / "tools" / "bus")
MSG = re.compile(r"^#(\d+) \S+ \[[\w-]+\] (\S+) -> \S+: (.*)$")


def send(to: str, subject: str, body: str = "") -> int:
    out = subprocess.run([BUS, "send", "--to", to, "--type", "request", "--subject", subject, "--body", body],
                         cwd=REPO, capture_output=True, text=True).stdout
    m = re.search(r"sent #(\d+)", out)
    return int(m.group(1)) if m else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--arm", action="append", required=True, metavar="AGENT:SITE ACTION...")
    ap.add_argument("--timeout", type=float, default=1200)
    args = ap.parse_args(argv)
    first, waiting = None, set()
    for spec in args.arm:
        agent, _, what = spec.partition(":")
        mid = send(agent, f"ARM {args.run} {what}", "Reply 'ARMED " + args.run + "' when armed (PLAN.md §6).")
        first = mid if first is None else min(first, mid)
        waiting.add(agent)
        print(f"ARM -> {agent}: {args.run} {what} (#{mid})", flush=True)
    end = time.time() + args.timeout
    while waiting and time.time() < end:
        log = subprocess.run([BUS, "log", "--since", str(first), "--limit", "1000"], cwd=REPO, capture_output=True,
                             text=True).stdout
        for line in log.splitlines():
            m = MSG.match(line)
            if m and m.group(2) in waiting and "ARMED" in m.group(3).upper() and args.run in m.group(3):
                print(f"ARMED <- {m.group(2)} (#{m.group(1)})", flush=True)
                waiting.discard(m.group(2))
        if waiting:
            time.sleep(10)
    if waiting:
        print(f"timeout: no ARMED from {sorted(waiting)}")
        return 1
    print(f"all armed for {args.run}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

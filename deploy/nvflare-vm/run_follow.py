#!/usr/bin/env python3
"""Follow central's "RUN start <name> <UTC>" / "RUN end <name> <UTC>" bus messages (stage-2 S4) on the
NVFlare VM. For every final run it
  - sends this VM's sysmon summary of the run window to central (deploy/nvflare-vm/sysmon.sh send),
  - scans what the NVFlare client logs (client + job logs of every site) appended during the run:
    rounds trained per site and anomalies (errors, tracebacks, job ends/aborts, lost server),
  - checks status.sh, records the outcome in ~/flhub-runtime/nvflare/follow/runs.jsonl, and reports to
    central only when something is flagged (silent when clean).

    nohup python3 deploy/nvflare-vm/run_follow.py [--since BUS_ID] &     # runs until killed

Reads the bus with `tools/bus log --since` (does not move the inbox cursor). Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BUS = str(REPO / "tools" / "bus")
SITES_DIR = Path.home() / "flhub-runtime" / "nvflare" / "sites"
STATE_DIR = Path.home() / "flhub-runtime" / "nvflare" / "follow"
HEAD = re.compile(r"^#(\d+) (\S+) \[([\w-]+)\] (\S+) -> (\S+): (.*)$")
RUN = re.compile(r"^RUN (start|end) (\S+) (\S+)")
ANOMALY = re.compile(r" - ERROR - |Traceback|Failing job|abort|END_RUN|no connection to server|target_unreachable")
TRAINED = re.compile(r"\[flhub\] (\S+): round (\d+) n=")


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def bus_messages(since: int) -> list[dict]:
    out = subprocess.run([BUS, "log", "--since", str(since), "--limit", "200"], capture_output=True, text=True,
                         timeout=60).stdout
    msgs = []
    for line in out.splitlines():
        m = HEAD.match(line)
        if m:
            msgs.append({"id": int(m[1]), "type": m[3], "from": m[4], "to": m[5], "subject": m[6]})
    return msgs


def log_files() -> list[Path]:
    """Client log and job logs of every site (job dirs appear when a relay job (re)starts)."""
    return sorted(SITES_DIR.glob("*/log.txt")) + sorted(SITES_DIR.glob("*/*-*-*-*-*/log.txt"))


def scan(offsets: dict[str, int]) -> tuple[list[str], dict[str, int], dict[str, int]]:
    """(flagged lines, anomaly counts per site, rounds trained per site) appended since `offsets`."""
    hits, counts, trained = [], {}, {}
    for path in log_files():
        site = path.relative_to(SITES_DIR).parts[0]
        with open(path, "rb") as f:
            f.seek(offsets.get(str(path), 0))
            text = f.read().decode(errors="replace")
        for ln in text.splitlines():
            if TRAINED.search(ln):
                trained[site] = trained.get(site, 0) + 1
            elif ANOMALY.search(ln):
                counts[site] = counts.get(site, 0) + 1
                if len(hits) < 12:
                    hits.append(f"{site}: {ln.strip()[:170]}")
    return hits, counts, trained


def offsets_now() -> dict[str, int]:
    return {str(p): p.stat().st_size for p in log_files()}


def health() -> str:
    proc = subprocess.run(["bash", str(REPO / "deploy" / "nvflare-vm" / "status.sh")], capture_output=True,
                          text=True, timeout=60)
    return (proc.stdout.strip().splitlines() or ["?"])[-1]


def main() -> None:
    ap = argparse.ArgumentParser(prog="run_follow.py")
    ap.add_argument("--since", type=int, default=None, help="bus id to start after (default: saved or newest)")
    args = ap.parse_args()
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    state_path = STATE_DIR / "state.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {"cursor": None, "open": {}}
    if args.since is not None:
        state["cursor"] = args.since
    if state["cursor"] is None:
        newest = bus_messages(0)
        state["cursor"] = newest[-1]["id"] if newest else 0
    print(f"{now()} following the bus after #{state['cursor']}", flush=True)
    while True:
        try:
            msgs = bus_messages(state["cursor"])
        except Exception as exc:  # bus briefly unreachable
            print(f"{now()} bus: {exc}", flush=True)
            time.sleep(30)
            continue
        for m in msgs:
            state["cursor"] = max(state["cursor"], m["id"])
            run = RUN.match(m["subject"]) if m["from"] == "central" and m["type"] == "info" else None
            if not run:
                continue
            kind, name, utc = run[1], run[2], run[3]
            if kind == "start":  # offsets of the previous poll (<= 15 s before): no run line is missed
                state["open"][name] = {"start": utc, "offsets": state.get("last_offsets") or offsets_now()}
                print(f"{now()} RUN start {name} {utc} (#{m['id']})", flush=True)
                continue
            opened = state["open"].pop(name, None)
            start = opened["start"] if opened else utc
            proc = subprocess.run(["bash", str(REPO / "deploy" / "nvflare-vm" / "sysmon.sh"), "send", name, start, utc],
                                  capture_output=True, text=True, timeout=300)
            hits, counts, trained = scan(opened["offsets"]) if opened else ([], {}, {})
            rec = {"run": name, "start": start, "end": utc, "bus_end": m["id"], "sysmon_rc": proc.returncode,
                   "sysmon_out": (proc.stdout + proc.stderr).strip()[-300:], "trained": trained,
                   "anomalies": counts, "health": health()}
            with open(STATE_DIR / "runs.jsonl", "a") as f:
                f.write(json.dumps(rec) + "\n")
            print(f"{now()} RUN end {name}: sysmon rc={proc.returncode}, trained {trained}, anomalies {counts}, "
                  f"{rec['health']}", flush=True)
            if proc.returncode != 0 or counts or rec["health"] != "status: HEALTHY":
                body = "\n".join([f"sysmon summary rc={proc.returncode}; {rec['health']}; rounds trained {trained}",
                                  f"NVFlare client log lines flagged during {start}..{utc}: {counts}"] + hits)
                subprocess.run([BUS, "send", "--to", "central", "--type", "info", "--subject",
                                f"S4 nvflare {name}: flagged NVFlare client log lines or health", "--body", body],
                               capture_output=True, text=True, timeout=60)
        state["last_offsets"] = offsets_now()
        state_path.write_text(json.dumps(state))
        time.sleep(15)


if __name__ == "__main__":
    main()

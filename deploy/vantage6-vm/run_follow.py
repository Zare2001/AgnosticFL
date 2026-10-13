#!/usr/bin/env python3
"""Follow central's "RUN start <name> <UTC>" / "RUN end <name> <UTC>" bus messages (stage-2 S4).

For every final run it
  - sends the vantage6 VM's sysmon summary of the run window to central (PLAN.md §4.3) with
    deploy/vantage6-vm/sysmon.sh summary <name> <start> <end>,
  - counts the rounds each node trained and scans the node logs of the window (docker logs
    --since/--until) for anomalies (errors, tracebacks, crashed runs, disconnects) and reports
    flagged lines or an unhealthy status to central (silent when clean),
  - records the outcome in ~/flhub-runtime/vantage6/follow/runs.jsonl.

    python3 deploy/vantage6-vm/run_follow.py [--since BUS_ID]     # runs until killed (nohup)

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
STATE_DIR = Path.home() / "flhub-runtime" / "vantage6" / "follow"
NODES = ["v6-h3", "v6-h4", "v6-h9", "v6-h10"]
HEAD = re.compile(r"^#(\d+) (\S+) \[([\w-]+)\] (\S+) -> (\S+): (.*)$")
RUN = re.compile(r"^RUN (start|end) (\S+) (\S+)")
ANOMALY = re.compile(r"ERROR|Traceback|Exception|crashed|non-zero exitcode|Disconnected from the server|"
                     r"IncompleteRead|Connection error|not allowed|non-existing Docker image")
TRAINED = re.compile(r"info > site=(\S+) org=\d+ round=(\d+) ")
ANSI = re.compile(r"\x1b\[[0-9;]*m")


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def bus_messages(since: int) -> list[dict]:
    out = subprocess.run([BUS, "log", "--since", str(since), "--limit", "200"], capture_output=True, text=True,
                         timeout=60).stdout
    msgs: list[dict] = []
    for line in out.splitlines():
        m = HEAD.match(line)
        if m:
            msgs.append({"id": int(m[1]), "type": m[3], "from": m[4], "to": m[5], "subject": m[6]})
    return msgs


def scan(start: str, end: str) -> tuple[list[str], dict, dict]:
    """(flagged lines, anomaly counts per node, rounds trained per node) of the node logs in the window."""
    hits, counts, rounds = [], {}, {}
    for node in NODES:
        proc = subprocess.run(["docker", "logs", "--since", start, "--until", end, f"vantage6-{node}-user"],
                              capture_output=True, text=True, timeout=120)
        if proc.returncode:  # node not running (E5 extras outside E5)
            continue
        lines = [ANSI.sub("", ln).strip() for ln in (proc.stdout + proc.stderr).splitlines()]
        bad = [ln for ln in lines if ANOMALY.search(ln) and "api_key" not in ln.lower()]
        counts[node] = len(bad)
        rounds[node] = len({m[2] for m in map(TRAINED.search, lines) if m})
        hits += [f"{node}: {ln[:160]}" for ln in bad[:4]]
    return hits, counts, rounds


def health() -> str:
    proc = subprocess.run(["bash", str(REPO / "deploy" / "vantage6-vm" / "status.sh")], capture_output=True,
                          text=True, timeout=60)
    return "HEALTHY" if proc.returncode == 0 else "UNHEALTHY: " + " | ".join(proc.stdout.strip().splitlines()[:4])


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
            if kind == "start":
                state["open"][name] = {"start": utc}
                print(f"{now()} RUN start {name} {utc} (#{m['id']})", flush=True)
                continue
            opened = state["open"].pop(name, None)
            start = opened["start"] if opened else utc
            proc = subprocess.run(["bash", str(REPO / "deploy" / "vantage6-vm" / "sysmon.sh"), "summary", name, start,
                                   utc], capture_output=True, text=True, timeout=300)
            hits, counts, rounds = scan(start, utc)
            rec = {"run": name, "start": start, "end": utc, "bus_end": m["id"], "sysmon_rc": proc.returncode,
                   "sysmon_out": (proc.stdout + proc.stderr).strip()[-300:], "anomalies": counts,
                   "rounds_trained": rounds, "health": health()}
            with open(STATE_DIR / "runs.jsonl", "a") as f:
                f.write(json.dumps(rec) + "\n")
            print(f"{now()} RUN end {name}: sysmon rc={proc.returncode}, rounds {rounds}, anomalies {counts}, "
                  f"{rec['health']}", flush=True)
            if proc.returncode != 0 or any(counts.values()) or rec["health"] != "HEALTHY":
                body = "\n".join([f"sysmon summary rc={proc.returncode}; {rec['health']}; rounds trained {rounds}",
                                  f"node log lines flagged during {start}..{utc}: {counts}"] + hits[:12])
                subprocess.run([BUS, "send", "--to", "central", "--type", "info", "--subject",
                                f"S4 vantage6 {name}: flagged node log lines or health", "--body", body],
                               capture_output=True, text=True, timeout=60)
        state_path.write_text(json.dumps(state))
        time.sleep(15)


if __name__ == "__main__":
    main()

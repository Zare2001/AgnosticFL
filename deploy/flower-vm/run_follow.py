#!/usr/bin/env python3
"""Follow central's "RUN start <name> <UTC>" / "RUN end <name> <UTC>" bus messages (stage-2 S4).

For every final run it
  - sends the flower VM's sysmon summary of the run window to central (PLAN.md §4.3) with
    deploy/flower-vm/sysmon.sh summary <name> <start> <end>,
  - scans what the hospital SuperNode logs appended during the run for anomalies (errors,
    tracebacks, reconnects, supervisor restarts) and reports them to central,
  - records the outcome in ~/flhub-runtime/flower/follow/runs.jsonl.

    python3 deploy/flower-vm/run_follow.py [--since BUS_ID]     # runs until killed (nohup)

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
STATE_DIR = Path.home() / "flhub-runtime" / "flower" / "follow"
LOG_GLOBS = [Path.home() / "flhub-runtime" / "flower" / "logs",
             Path.home() / "flhub-runtime" / "flower-e5" / "logs"]
HEAD = re.compile(r"^#(\d+) (\S+) \[([\w-]+)\] (\S+) -> (\S+): (.*)$")
RUN = re.compile(r"^RUN (start|end) (\S+) (\S+)")
ANOMALY = re.compile(r"ERROR|Traceback|Exception|exited rc=|Connection attempt failed|UNAUTHENTICATED|"
                     r"Error:|went offline|NODE_UNAVAILABLE|CLIENT_APP_CRASHED")
ANSI = re.compile(r"\x1b\[[0-9;]*m")
PERF_RUNS = ("s2-e3-", "s2-e4a-", "s2-e4b-", "s2-e5-", "s2-e7-")  # also post VM memory/bytes per run
SUMMARIES = Path.home() / "flhub-runtime" / "sysmon" / "summaries"
MEM_TOTAL_MB = 32097.0


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


def log_files() -> list[Path]:
    return [p for d in LOG_GLOBS if d.is_dir() for p in sorted(d.glob("supernode-*.log"))]


def scan(offsets: dict[str, int]) -> tuple[list[str], dict[str, int]]:
    """Anomalous lines appended since `offsets` (per SuperNode log) -> (lines, counts)."""
    hits, counts = [], {}
    for path in log_files():
        start = offsets.get(str(path), 0)
        with open(path, "rb") as f:
            f.seek(start)
            text = f.read().decode(errors="replace")
        site = path.stem.removeprefix("supernode-")
        bad = [ANSI.sub("", ln).strip() for ln in text.splitlines() if ANOMALY.search(ln)]
        counts[site] = len(bad)
        hits += [f"{site}: {ln[:160]}" for ln in bad[:4]]
    return hits, counts


def post_perf(name: str, start: str, end: str) -> None:
    """One line per performance run: VM memory peak (70 % rule), per-site RSS peaks, bytes on ens3."""
    try:
        d = json.loads((SUMMARIES / f"sysmon-flower-{name}.json").read_text())
    except (OSError, ValueError):
        return
    mem = d["system"]["mem_used_mb_peak"]
    procs = d.get("procs", {})
    sites = {s: round(sum(procs.get(f"{s}-{c}", {}).get("rss_mb_peak", 0) for c in ("supernode", "superexec",
                                                                                    "clientapp")))
             for s in ("flw-h5", "flw-h6", "flw-h11", "flw-h12")}
    net = d.get("net", {}).get(d.get("main_iface", "ens3"), {})
    body = (f"window {start}..{end}; VM mem peak {mem / 1024:.1f} GB = {100 * mem / MEM_TOTAL_MB:.0f} % of 31 GB "
            f"(70 % rule {'ok' if mem < 0.7 * MEM_TOTAL_MB else 'EXCEEDED'}); cpu peak {d['system']['cpu_peak']} %; "
            f"RSS peak MB per site (supernode+superexec+clientapp) {{{', '.join(f'{k}: {v}' for k, v in sites.items() if v)}}}; "
            f"ens3 rx {net.get('rx_bytes', 0) / 1e6:.1f} MB tx {net.get('tx_bytes', 0) / 1e6:.1f} MB")
    subprocess.run([BUS, "send", "--to", "central", "--type", "info", "--subject",
                    f"S4 flower VM {name}: mem peak {mem / 1024:.1f} GB ({100 * mem / MEM_TOTAL_MB:.0f} %)",
                    "--body", body], capture_output=True, text=True, timeout=60)


def health() -> str:
    proc = subprocess.run(["bash", str(REPO / "deploy" / "flower-vm" / "status.sh")], capture_output=True,
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
            if kind == "start":
                state["open"][name] = {"start": utc, "offsets": {str(p): p.stat().st_size for p in log_files()}}
                print(f"{now()} RUN start {name} {utc} (#{m['id']})", flush=True)
                continue
            opened = state["open"].pop(name, None)
            start = opened["start"] if opened else utc
            proc = subprocess.run(["bash", str(REPO / "deploy" / "flower-vm" / "sysmon.sh"), "summary", name, start, utc],
                                  capture_output=True, text=True, timeout=300)
            hits, counts = scan(opened["offsets"]) if opened else ([], {})
            rec = {"run": name, "start": start, "end": utc, "bus_end": m["id"], "sysmon_rc": proc.returncode,
                   "sysmon_out": (proc.stdout + proc.stderr).strip()[-300:], "anomalies": counts, "health": health()}
            with open(STATE_DIR / "runs.jsonl", "a") as f:
                f.write(json.dumps(rec) + "\n")
            print(f"{now()} RUN end {name}: sysmon rc={proc.returncode}, anomalies {counts}, {rec['health']}", flush=True)
            if name.startswith(PERF_RUNS):
                post_perf(name, start, utc)
            if proc.returncode != 0 or any(counts.values()) or not rec["health"].endswith(": HEALTHY"):
                body = "\n".join([f"sysmon summary rc={proc.returncode}; {rec['health']}",
                                  f"SuperNode log lines flagged during {start}..{utc}: {counts}"] + hits[:12])
                subprocess.run([BUS, "send", "--to", "central", "--type", "info", "--subject",
                                f"S4 flower {name}: flagged SuperNode log lines or health", "--body", body],
                               capture_output=True, text=True, timeout=60)
        state_path.write_text(json.dumps(state))
        time.sleep(15)


if __name__ == "__main__":
    main()

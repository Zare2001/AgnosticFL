#!/usr/bin/env python3
"""System monitor for stage-2 runs (PLAN.md stage 2 §4.3). Standard library only (reads /proc).

    python3 scripts/sysmon.py run [--dir ~/flhub-runtime/sysmon] [--interval 1] [--keep-hours 48]
                                  [--proc NAME=REGEX ...]
    python3 scripts/sysmon.py summarize --from 2026-10-14T10:00:00Z --to 2026-10-14T10:30:00Z
                                  [--dir ...] [--run NAME] [-o summary.json]
    python3 scripts/sysmon.py status [--dir ...]

`run` samples every second: system CPU % and used memory, CPU % (100 = one core) and RSS of the
named components below (all processes whose command line matches, summed; docker containers
included), and the byte counters of every network interface. Samples go to hourly JSONL files
(sysmon-YYYYmmddTHH.jsonl, UTC) that are deleted after --keep-hours. `summarize` cuts a window
(one run, from the bus "RUN start"/"RUN end" times) into a small JSON: peak and mean CPU and RSS
per component, bytes in/out per interface (counter deltas; the main interface is the one of the
default route). Send it to central with: tools/bus put <file> --to central
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DIR = Path.home() / "flhub-runtime" / "sysmon"
# component name -> regex on the process command line (first match wins)
COMPONENTS = [
    ("orchestrator", r"hub\.orchestrator"),
    ("hub-sim", r"hub\.sim"),
    ("hub-core", r"-m hub\.core|hub/core"),
    ("adapter-nvflare", r"adapters\.nvflare"),
    ("adapter-vantage6", r"adapters\.vantage6"),
    ("adapter-flower", r"adapters\.flower"),
    ("nvflare-server", r"nvflare\.private\.fed\.app\.server\.server_train"),
    ("nvflare-server-job", r"nvflare\.private\.fed\.app\.server\.runner_process|server_app_runner"),
    ("nvflare-client", r"nvflare\.private\.fed\.app\.client\.client_train"),
    ("nvflare-client-job", r"nvflare\.private\.fed\.app\.client\.worker_process|client_app_runner"),
    ("nvflare-native", r"nvflare.*(native|fedavg)"),
    ("v6-server", r"vserver|vantage6\.server|uwsgi"),
    ("v6-node", r"vnode|vantage6\.node"),
    ("v6-algorithm", r"v6_flhub|wrap_algorithm"),
    ("flower-superlink", r"flower-superlink"),
    ("flower-supernode", r"flower-supernode"),
    ("flower-superexec", r"flower-superexec"),
    ("flower-serverapp", r"flwr-serverapp"),
    ("flower-clientapp", r"flwr-clientapp"),
    ("flower-flwr-run", r"flwr run"),
    ("registry", r"registry serve"),
    ("dockerd", r"dockerd|containerd"),
    ("agentbus", r"agentbus"),
    ("sysmon", r"sysmon\.py"),
]
TICKS = os.sysconf("SC_CLK_TCK")
PAGE_MB = os.sysconf("SC_PAGE_SIZE") / 2**20


def utc(ts: float | None = None) -> str:
    return datetime.fromtimestamp(time.time() if ts is None else ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def parse_utc(s: str) -> float:
    s = s.strip().replace("Z", "+00:00")
    return datetime.fromisoformat(s).timestamp()


def read(path: str) -> str:
    with open(path, "rb") as f:
        return f.read().decode("utf-8", "replace")


def cpu_total() -> tuple[float, float]:
    """(busy, total) jiffies of all CPUs."""
    v = [float(x) for x in read("/proc/stat").split("\n", 1)[0].split()[1:]]
    idle = v[3] + (v[4] if len(v) > 4 else 0.0)
    return sum(v) - idle, sum(v)


def mem_used_mb() -> float:
    info = {}
    for line in read("/proc/meminfo").splitlines():
        k, _, rest = line.partition(":")
        info[k] = float(rest.split()[0]) / 1024.0
    return info["MemTotal"] - info.get("MemAvailable", info.get("MemFree", 0.0))


def net_bytes() -> dict[str, list[int]]:
    out = {}
    for line in read("/proc/net/dev").splitlines()[2:]:
        name, _, rest = line.partition(":")
        name = name.strip()
        if name == "lo":
            continue
        f = rest.split()
        out[name] = [int(f[0]), int(f[8])]  # rx bytes, tx bytes
    return out


def default_iface() -> str | None:
    try:
        for line in read("/proc/net/route").splitlines()[1:]:
            f = line.split()
            if f[1] == "00000000":
                return f[0]
    except OSError:
        pass
    return None


class Sampler:
    def __init__(self, components: list[tuple[str, str]]) -> None:
        self.components = [(n, re.compile(rx)) for n, rx in components]
        self.names: dict[int, str | None] = {}
        self.prev_proc: dict[int, float] = {}
        self.prev_cpu = cpu_total()
        self.prev_t = time.monotonic()

    def name_of(self, pid: int) -> str | None:
        if pid not in self.names:
            try:
                cmd = read(f"/proc/{pid}/cmdline").replace("\0", " ").strip()
            except OSError:
                cmd = ""
            self.names[pid] = next((n for n, rx in self.components if cmd and rx.search(cmd)), None)
        return self.names[pid]

    def sample(self) -> dict:
        now, mono = time.time(), time.monotonic()
        busy, total = cpu_total()
        dt_wall = max(mono - self.prev_t, 1e-6)
        sys_cpu = 100.0 * (busy - self.prev_cpu[0]) / max(total - self.prev_cpu[1], 1e-6)
        procs: dict[str, list[float]] = {}
        alive, cur = set(), {}
        for entry in os.scandir("/proc"):
            if not entry.name.isdigit():
                continue
            pid = int(entry.name)
            name = self.name_of(pid)
            alive.add(pid)
            if name is None:
                continue
            try:
                stat = read(f"/proc/{pid}/stat")
                fields = stat[stat.rindex(")") + 2:].split()
                jiffies = float(fields[11]) + float(fields[12])  # utime + stime
                rss_mb = float(fields[21]) * PAGE_MB
            except (OSError, ValueError, IndexError):
                continue
            cur[pid] = jiffies
            cpu = 100.0 * (jiffies - self.prev_proc.get(pid, jiffies)) / TICKS / dt_wall
            agg = procs.setdefault(name, [0.0, 0.0, 0])
            agg[0] += cpu
            agg[1] += rss_mb
            agg[2] += 1
        for pid in list(self.names):
            if pid not in alive:
                del self.names[pid]
        self.prev_proc, self.prev_cpu, self.prev_t = cur, (busy, total), mono
        return {"t": round(now, 3), "utc": utc(now), "cpu": round(sys_cpu, 1), "mem_used_mb": round(mem_used_mb(), 1),
                "net": net_bytes(), "procs": {k: [round(v[0], 1), round(v[1], 1), v[2]] for k, v in procs.items()}}


def file_for(directory: Path, ts: float) -> Path:
    return directory / f"sysmon-{datetime.fromtimestamp(ts, timezone.utc):%Y%m%dT%H}.jsonl"


def cmd_run(args) -> None:
    directory = Path(args.dir).expanduser()
    directory.mkdir(parents=True, exist_ok=True)
    comps = [tuple(p.split("=", 1)) for p in args.proc] + COMPONENTS
    sampler = Sampler(comps)
    meta = {"host": socket.gethostname(), "iface": default_iface(), "interval_s": args.interval, "started": utc()}
    (directory / "sysmon.meta.json").write_text(json.dumps(meta))
    next_t, last_cleanup = time.monotonic(), 0.0
    while True:
        next_t += args.interval
        time.sleep(max(0.0, next_t - time.monotonic()))
        s = sampler.sample()
        with open(file_for(directory, s["t"]), "a") as f:
            f.write(json.dumps(s, separators=(",", ":")) + "\n")
        if s["t"] - last_cleanup > 600:
            last_cleanup = s["t"]
            for p in directory.glob("sysmon-*.jsonl"):
                if p.stat().st_mtime < s["t"] - args.keep_hours * 3600:
                    p.unlink(missing_ok=True)


def samples(directory: Path, t0: float, t1: float):
    hours = range(int(t0 // 3600) - 1, int(t1 // 3600) + 1)
    for h in hours:
        p = file_for(directory, h * 3600 + 1)
        if not p.exists():
            continue
        for line in p.read_text().splitlines():
            try:
                s = json.loads(line)
            except ValueError:
                continue
            if t0 <= s["t"] <= t1:
                yield s


def summarize(directory: Path, t0: float, t1: float, run: str | None = None) -> dict:
    rows = list(samples(directory, t0, t1))
    meta_path = directory / "sysmon.meta.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    out = {"host": meta.get("host", socket.gethostname()), "run": run, "from": utc(t0), "to": utc(t1),
           "samples": len(rows), "main_iface": meta.get("iface") or default_iface()}
    if not rows:
        return out
    cpu = [r["cpu"] for r in rows]
    mem = [r["mem_used_mb"] for r in rows]
    out["system"] = {"cpu_mean": round(sum(cpu) / len(cpu), 1), "cpu_peak": max(cpu),
                     "mem_used_mb_peak": max(mem), "mem_used_mb_mean": round(sum(mem) / len(mem), 1)}
    procs: dict[str, dict] = {}
    for r in rows:
        for name, (c, rss, n) in r["procs"].items():
            p = procs.setdefault(name, {"cpu": [], "rss": [], "n": 0})
            p["cpu"].append(c)
            p["rss"].append(rss)
            p["n"] = max(p["n"], n)
    out["procs"] = {name: {"cpu_mean": round(sum(p["cpu"]) / len(rows), 1), "cpu_peak": max(p["cpu"]),
                           "rss_mb_peak": round(max(p["rss"]), 1), "rss_mb_mean": round(sum(p["rss"]) / len(p["rss"]), 1),
                           "max_processes": p["n"], "seen_s": len(p["cpu"])} for name, p in sorted(procs.items())}
    first, last = rows[0]["net"], rows[-1]["net"]
    out["net"] = {i: {"rx_bytes": last[i][0] - first[i][0], "tx_bytes": last[i][1] - first[i][1]}
                  for i in last if i in first}
    out["duration_s"] = round(rows[-1]["t"] - rows[0]["t"], 1)
    return out


def cmd_summarize(args) -> None:
    res = summarize(Path(args.dir).expanduser(), parse_utc(args.start), parse_utc(args.end), args.run)
    text = json.dumps(res, indent=1)
    if args.output:
        Path(args.output).write_text(text + "\n")
    print(text)


def cmd_status(args) -> None:
    directory = Path(args.dir).expanduser()
    files = sorted(directory.glob("sysmon-*.jsonl"))
    if not files:
        print("no samples")
        sys.exit(1)
    last = files[-1].read_text().splitlines()[-1]
    age = time.time() - json.loads(last)["t"]
    print(f"{len(files)} files, last sample {age:.1f}s ago ({files[-1].name})")
    sys.exit(0 if age < 30 else 1)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="sysmon.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--dir", default=str(DEFAULT_DIR))
    r.add_argument("--interval", type=float, default=1.0)
    r.add_argument("--keep-hours", type=float, default=48.0)
    r.add_argument("--proc", nargs="*", default=[], metavar="NAME=REGEX")
    s = sub.add_parser("summarize")
    s.add_argument("--dir", default=str(DEFAULT_DIR))
    s.add_argument("--from", dest="start", required=True)
    s.add_argument("--to", dest="end", required=True)
    s.add_argument("--run")
    s.add_argument("-o", "--output")
    st = sub.add_parser("status")
    st.add_argument("--dir", default=str(DEFAULT_DIR))
    args = ap.parse_args(argv)
    {"run": cmd_run, "summarize": cmd_summarize, "status": cmd_status}[args.cmd](args)


if __name__ == "__main__":
    main()

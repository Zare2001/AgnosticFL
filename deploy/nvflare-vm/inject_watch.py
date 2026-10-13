"""One-shot watcher behind inject.sh (stdlib only, system python3).

Follows the job logs (<ws>/<job id>/log.txt) of one NVFlare site, also those of relay jobs that
start later. Round N has started at the first "got task assignment" after the site printed
"[flhub] <site>: round N-1" (for N = 1: the first task after arming). Then it waits --after
seconds, announces the injection on the agent bus, acts, and records the client's recovery:
new client process, registration, job process back.
"""

import argparse
import glob
import json
import os
import re
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone

ASSIGN = re.compile(r"got task assignment: name=train\b")
RECOVERY_WAIT_S = 900


def utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def say(msg: str) -> None:
    print(f"{utc()} {msg}", flush=True)


class JobLogs:
    """New lines of every job log of the site, in file modification order."""

    def __init__(self, ws: str) -> None:
        self.ws = ws
        self.offsets = {p: os.path.getsize(p) for p in self._paths()}  # only lines written after arming

    def _paths(self) -> list:
        return glob.glob(os.path.join(self.ws, "*", "log.txt"))

    def lines(self) -> list:
        out = []
        for path in sorted(self._paths(), key=lambda p: os.path.getmtime(p) if os.path.exists(p) else 0):
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            off = self.offsets.get(path, 0)
            if size < off:
                off = 0
            if size == off:
                continue
            with open(path, "rb") as f:
                f.seek(off)
                data = f.read(size - off)
            cut = data.rfind(b"\n") + 1  # keep a partial last line for the next call
            self.offsets[path] = off + cut
            out += [(path, line) for line in data[:cut].decode("utf-8", "replace").splitlines()]
        return out


def read_pid(path: str):
    try:
        with open(path) as f:
            pid = int(f.read().strip())
        os.kill(pid, 0)
        return pid
    except (OSError, ValueError):
        return None


def worker_pids(ws: str) -> list:
    out = subprocess.run(["pgrep", "-f", f"nvflare.private.fed.app.client.worker_process.*{ws}/"],
                         capture_output=True, text=True).stdout
    return [int(p) for p in out.split()]


def announce(bus: str, subject: str, body: str) -> None:
    try:  # fire and forget: the injection must not wait for the bus
        subprocess.Popen([bus, "send", "--to", "all", "--type", "info", "--subject", subject, "--body", body],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError as e:
        say(f"bus announcement failed: {e}")


def act(args) -> dict:
    ws = args.ws
    client = read_pid(os.path.join(ws, "pid.fl"))
    workers = worker_pids(ws)
    done = {"action": args.action, "client_pid": client, "worker_pids": workers}
    if args.action.startswith("straggler-"):
        cmd = ["bash", args.straggler, "off", args.site] if args.action == "straggler-off" else \
            ["bash", args.straggler, "on", args.site, args.action.split(":", 1)[1]]
        out = subprocess.run(cmd, capture_output=True, text=True)
        done.update(rc=out.returncode, output=(out.stdout + out.stderr).strip()[-400:])
        return done
    if args.action == "restart":
        open(os.path.join(ws, "restart.fl"), "w").close()
        return done
    targets = workers if args.action == "kill-job" else ([client] if client else []) + workers
    for pid in targets:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    done["killed"] = targets
    return done


def watch_recovery(args, done: dict, t_act: float) -> dict:
    """Client-side recovery timeline after the injection (seconds after the action)."""
    ws, rec = args.ws, {}
    main_log = os.path.join(ws, "log.txt")
    log_from = os.path.getsize(main_log) if os.path.exists(main_log) else 0
    old_client, old_workers = done.get("client_pid"), set(done.get("worker_pids") or [])
    expect = {"job_s"} if args.action == "kill-job" else {"client_up_s", "registered_s", "job_s"}
    end = time.monotonic() + RECOVERY_WAIT_S
    while time.monotonic() < end and not expect <= set(rec):
        now = round(time.monotonic() - t_act, 1)
        client = read_pid(os.path.join(ws, "pid.fl"))
        if "client_up_s" not in rec and client and client != old_client:
            rec["client_up_s"] = now
            say(f"recovery: new client process {client} after {now}s")
        if "registered_s" not in rec and os.path.exists(main_log):
            with open(main_log, "rb") as f:
                f.seek(log_from)
                if b"Successfully registered client" in f.read():
                    rec["registered_s"] = now
                    say(f"recovery: client registered with the server after {now}s")
        if "job_s" not in rec and ("registered_s" in rec or args.action == "kill-job") \
                and set(worker_pids(ws)) - old_workers:
            rec["job_s"] = now
            say(f"recovery: a job process runs again after {now}s (site back in a relay job)")
        time.sleep(1)
    return rec


def main() -> None:
    ap = argparse.ArgumentParser()
    for a in ("--ws", "--site", "--action", "--state", "--bus"):
        ap.add_argument(a, required=True)
    ap.add_argument("--straggler", default="straggler.sh")
    ap.add_argument("--round", type=int, required=True)
    ap.add_argument("--after", type=float, default=0.0)
    ap.add_argument("--max-wait", type=float, default=21600.0)
    args = ap.parse_args()
    signal.signal(signal.SIGTERM, lambda *_: (say("disarmed"), sys.exit(0)))
    end_re = re.compile(rf"\[flhub\] {re.escape(args.site)}: round (\d+) n=")
    logs = JobLogs(args.ws)
    say(f"armed: {args.action} {args.site} at round {args.round} +{args.after}s; following {args.ws}/*/log.txt")
    prev_done = args.round == 1
    deadline = time.monotonic() + args.max_wait
    started = None
    while started is None:
        if time.monotonic() > deadline:
            say("expired: round never started at this site")
            os.unlink(args.state)
            return
        for path, line in logs.lines():
            m = end_re.search(line)
            if m:
                r = int(m.group(1))
                if r == args.round - 1:
                    prev_done = True
                    say(f"round {r} finished at {args.site}: next task is round {args.round}")
                elif r >= args.round:
                    say(f"missed: round {r} finished without a detected start of round {args.round}")
                    os.unlink(args.state)
                    return
            elif prev_done and ASSIGN.search(line):
                started = (time.monotonic(), line[:23])
                say(f"round {args.round} started at {args.site} (log time {line[:23]}, {path})")
                break
        time.sleep(0.2)
    time.sleep(max(0.0, started[0] + args.after - time.monotonic()))
    t_act, when = time.monotonic(), utc()
    announce(args.bus, f"E6 inject {args.action} {args.site} at {when}",
             f"round {args.round} +{args.after}s (round start at {args.site}: {started[1]} local log time)")
    done = act(args)
    say(f"FIRED {args.action} at {when}: {json.dumps(done)}")
    os.unlink(args.state)
    rec = {} if args.action.startswith("straggler-") else watch_recovery(args, done, t_act)
    with open(os.path.join(os.path.dirname(args.state), f"{args.site}.result.json"), "w") as f:
        json.dump({"site": args.site, "round": args.round, "after_s": args.after, "fired_at": when,
                   **done, "recovery": rec}, f, indent=1)
    say(f"done: recovery {rec}")


if __name__ == "__main__":
    main()

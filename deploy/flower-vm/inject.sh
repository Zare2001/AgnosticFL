#!/usr/bin/env bash
# E6 fault injection on the flower VM (EXPERIMENTS.md E6, PLAN.md §8.5). One-shot: arm, wait for
# hub round N to reach one site, wait S seconds, act, log, exit.
# Trigger: the site's SuperNode log shows "[RUN <id>, ROUND <N>]" followed by "Receiving: train
# message" (the relay sends hub round N as the Flower message group id).
# Usage:
#   bash deploy/flower-vm/inject.sh arm <site> <action> --round N [--after S]
#   bash deploy/flower-vm/inject.sh disarm [<site>] | status
#   actions: kill       SIGKILL the site's ClientApp process (flwr-clientapp, the training process);
#                       the SuperNode stays up and replies CLIENT_APP_CRASHED once the ClientApp's
#                       task heartbeat expires
#            kill-node  SIGKILL the site's SuperNode process tree (flower-supernode, flower-superexec,
#                       flwr-clientapp); its supervisor supernode.sh restarts it after 10 s (new node id)
#            restart    SIGTERM the site's flower-supernode (graceful exit); the supervisor restarts it
#            straggler-on:<cpu%> | straggler-off   run straggler.sh on|off for the site at that moment
# Logs: ~/flhub-runtime/flower/e6/inject-<site>.log (UTC times of arm, trigger, action, recovery).
# Announces "E6 inject <action> <site> at <UTC>" on the bus as it acts (FLHUB_E6_BUS=0: island tests, no bus).
set -euo pipefail
. "$(dirname "$0")/common.sh"
DIR="$RUNTIME/e6"
mkdir -p "$DIR"
now() { date -u +%Y-%m-%dT%H:%M:%S.%2NZ; }

read -r -d '' WATCHER <<'PY' || true
import os, signal, subprocess, sys, time
from datetime import datetime, timezone

site, action, rnd, after, slog, pidf, repo = sys.argv[1:8]

def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-4] + "Z"

def log(msg):
    print(f"{now()} {msg}", flush=True)

def children(pid):
    out = subprocess.run(["pgrep", "-P", str(pid)], capture_output=True, text=True).stdout.split()
    return [int(p) for p in out]

def tree(pid):
    out = []
    for c in children(pid):
        out += [c] + tree(c)
    return out

def cmdline(pid):
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            return f.read().replace(b"\0", b" ").decode(errors="replace")
    except OSError:
        return ""

def supernode():
    try:
        sup = int(open(pidf).read().strip())
    except (OSError, ValueError):
        return None
    return next((c for c in children(sup) if "flower-supernode" in cmdline(c)), None)

class Follow:
    """Complete new lines appended to a file from its current end."""
    def __init__(self, path):
        self.f = open(path, "rb")
        self.f.seek(0, 2)
        self.buf = b""
    def lines(self, timeout):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            chunk = self.f.read()
            if chunk:
                self.buf += chunk
                *done, self.buf = self.buf.split(b"\n")
                for raw in done:
                    yield raw.decode(errors="replace")
            else:
                time.sleep(0.05)

follow = Follow(slog)
pattern, prev = f", ROUND {rnd}]", ""
for line in follow.lines(float("inf")):
    if "Receiving: train message" in line and pattern in prev:
        break
    prev = line
log(f"trigger: hub round {rnd} train message received by {site}; acting in {after} s")
time.sleep(float(after))

node = supernode()
if action.startswith("straggler-"):
    targets = []
else:
    if node is None:
        log(f"FAILED: no running flower-supernode for {site}")
        sys.exit(1)
    procs = tree(node)
    if action == "kill":
        targets = [p for p in procs if "flwr-clientapp" in cmdline(p)]
    elif action == "kill-node":
        targets = [node] + procs
    else:  # restart
        targets = [node]
    if not targets:
        log(f"FAILED: no {action} target for {site} (training already finished?)")
        sys.exit(1)
subject = f"E6 inject {action} {site} at {now()} (hub round {rnd} +{after}s)"
if os.environ.get("FLHUB_E6_BUS", "1") != "0":  # island tests set FLHUB_E6_BUS=0
    subprocess.Popen([f"{repo}/tools/bus", "send", "--to", "all", "--type", "info", "--subject", subject],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
t_act = time.monotonic()
if action.startswith("straggler-"):
    verb = "on" if action.startswith("straggler-on") else "off"
    args = [f"{repo}/deploy/flower-vm/straggler.sh", verb, site]
    if verb == "on":
        args.append(action.split(":", 1)[1])
    rc = subprocess.run(["bash", *args]).returncode
    log(f"action {action}: straggler.sh {verb} {site} exit={rc}")
    sys.exit(rc)
sig = signal.SIGTERM if action == "restart" else signal.SIGKILL
what = cmdline(targets[0])[:70]
for p in targets:
    try:
        os.kill(p, sig)
    except ProcessLookupError:
        pass
log(f"action {action}: {sig.name} -> pids {targets} (first: {what})")

# Recovery evidence from the SuperNode log (UTC times; seconds after the action).
want = "SuperNode ID:" if action in ("kill-node", "restart") else "Sending: train message"
for line in follow.lines(600):
    if want in line:
        what = "SuperNode registered again" if want.startswith("SuperNode") else "reply sent (error reply)"
        log(f"recovery: {what} after {time.monotonic() - t_act:.1f} s: {line.split(':', 1)[-1].strip()[:80]}")
        break
else:
    log("recovery: nothing seen in the SuperNode log within 600 s")
PY

case "${1:-}" in
  arm)
    site="${2:?site}"; action="${3:?action}"; shift 3
    round=""; after=0
    while [ $# -gt 0 ]; do
      case "$1" in
        --round) round="$2"; shift 2 ;;
        --after) after="$2"; shift 2 ;;
        *) echo "unknown option $1" >&2; exit 2 ;;
      esac
    done
    [ -n "$round" ] || { echo "--round N is required" >&2; exit 2; }
    case "$action" in
      kill|kill-node|restart|straggler-off) ;;
      straggler-on:[0-9]*) ;;
      *) echo "unknown action $action" >&2; exit 2 ;;
    esac
    [ -n "$(node_port "$site")" ] || { echo "unknown site $site (sites: $NODES)" >&2; exit 2; }
    slog="$RUNTIME/logs/supernode-$site.log"
    [ -f "$slog" ] || { echo "no SuperNode log $slog" >&2; exit 1; }
    if pid_alive "$DIR/inject-$site.pid"; then
      echo "already armed for $site (disarm first)" >&2; exit 1
    fi
    echo "$(now) armed site=$site action=$action round=$round after=${after}s" >> "$DIR/inject-$site.log"
    setsid nohup /usr/bin/python3 -u -c "$WATCHER" "$site" "$action" "$round" "$after" "$slog" \
        "$RUNTIME/run/supernode-$site.pid" "$REPO" >> "$DIR/inject-$site.log" 2>&1 < /dev/null &
    echo $! > "$DIR/inject-$site.pid"
    echo "ARMED $site $action at hub round $round +${after}s (watcher pid $!, log $DIR/inject-$site.log)"
    ;;
  disarm)
    for f in "$DIR"/inject-${2:-*}.pid; do
      [ -f "$f" ] || continue
      s="$(basename "$f" .pid)"; s="${s#inject-}"
      if pid_alive "$f"; then kill "$(cat "$f")"; echo "$(now) disarmed" >> "$DIR/inject-$s.log"; echo "disarmed $s"; fi
      rm -f "$f"
    done
    ;;
  status)
    for f in "$DIR"/inject-*.log; do
      [ -f "$f" ] || continue
      s="$(basename "$f" .log)"; s="${s#inject-}"
      pid_alive "$DIR/inject-$s.pid" && st="ARMED (pid $(cat "$DIR/inject-$s.pid"))" || st="idle"
      echo "$s: $st"; tail -n 4 "$f" | sed 's/^/    /'
    done
    ;;
  *) echo "usage: $0 arm <site> <kill|kill-node|restart|straggler-on:<cpu%>|straggler-off> --round N [--after S] | disarm [<site>] | status" >&2; exit 2 ;;
esac

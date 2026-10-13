#!/usr/bin/env bash
# E6 fault injection for the NVFlare hospitals on this VM (PLAN.md §8, EXPERIMENTS.md E6).
#   inject.sh arm <site> <kill|kill-job|restart> --round N [--after S]
#   inject.sh disarm [<site>]
#   inject.sh status
# arm starts a one-shot watcher that follows the site's client log. Round N has started when the
# site gets its first task after it finished round N-1 ("[flhub] <site>: round N-1", printed by
# clients/nvflare/client.py) - exact when the site takes part in every round, as in E6a/E6e.
# S seconds later the watcher announces the injection on the bus and acts:
#   kill      SIGKILL the client process and its job processes (hospital crash); the kit's own
#             daemon restarts the client, the cron watchdog covers a dead daemon
#   kill-job  SIGKILL only the training (job worker) process; the client process stays up
#   restart   the kit's standard restart: touch restart.fl, the daemon restarts the client
# A watcher that never fires gives up after --max-wait (default 6 h). Results: $NVF_INJECT_DIR.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/common.sh"
NVF_INJECT_DIR="${NVF_INJECT_DIR:-$(dirname "$NVF_SITES_DIR")/inject}"
NVF_INJECT_BUS="${NVF_INJECT_BUS:-$REPO/tools/bus}"  # island tests: NVF_INJECT_BUS=true (no announcement)
mkdir -p "$NVF_INJECT_DIR"

usage() { sed -n '2,5p' "$0" | sed 's/^# \{0,1\}//' >&2; exit 2; }

disarm_site() {
  local f="$NVF_INJECT_DIR/$1.armed" pid
  [ -f "$f" ] || return 0
  pid=$(awk -F= '$1 == "pid" {print $2}' "$f")
  if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then kill "$pid" 2>/dev/null || true; fi
  rm -f "$f"
  echo "inject: $1 disarmed"
}

cmd="${1:-}"; shift || true
case "$cmd" in
  arm)
    [ $# -ge 2 ] || usage
    site=$1 action=$2; shift 2
    round="" after=0 max_wait=21600
    while [ $# -gt 0 ]; do
      case "$1" in
        --round) round=$2; shift 2 ;;
        --after) after=$2; shift 2 ;;
        --max-wait) max_wait=$2; shift 2 ;;
        *) usage ;;
      esac
    done
    [[ "$round" =~ ^[0-9]+$ && "$round" -ge 1 ]] || { echo "inject: --round N (N >= 1) is required" >&2; exit 2; }
    [[ "$after" =~ ^[0-9]+([.][0-9]+)?$ ]] || { echo "inject: --after must be seconds" >&2; exit 2; }
    case "$action" in kill|kill-job|restart) ;; *) echo "inject: unknown action $action" >&2; exit 2 ;; esac
    ws="$NVF_SITES_DIR/$site"
    [ -f "$ws/startup/fed_client.json" ] || { echo "inject: no NVFlare site $site in $NVF_SITES_DIR" >&2; exit 1; }
    disarm_site "$site" >/dev/null
    log="$NVF_INJECT_DIR/$site-$(date -u +%Y%m%dT%H%M%SZ).log"
    setsid nohup python3 "$HERE/inject_watch.py" --ws "$ws" --site "$site" --action "$action" --round "$round" \
      --after "$after" --max-wait "$max_wait" --state "$NVF_INJECT_DIR/$site.armed" --bus "$NVF_INJECT_BUS" \
      > "$log" 2>&1 < /dev/null &
    pid=$!
    printf 'pid=%s\nsite=%s\naction=%s\nround=%s\nafter=%s\narmed_at=%s\nlog=%s\n' \
      "$pid" "$site" "$action" "$round" "$after" "$(date -u +%FT%TZ)" "$log" > "$NVF_INJECT_DIR/$site.armed"
    sleep 1
    kill -0 "$pid" 2>/dev/null || { echo "inject: watcher exited at once:" >&2; cat "$log" >&2; exit 1; }
    echo "inject: ARMED $site $action at round $round +${after}s (watcher pid $pid, log $log)"
    ;;
  disarm)
    if [ $# -ge 1 ]; then disarm_site "$1"; else
      for f in "$NVF_INJECT_DIR"/*.armed; do [ -e "$f" ] && disarm_site "$(basename "$f" .armed)"; done
    fi
    echo "inject: nothing armed beyond this point"
    ;;
  status)
    any=0
    for f in "$NVF_INJECT_DIR"/*.armed; do
      [ -e "$f" ] || continue
      any=1; pid=$(awk -F= '$1 == "pid" {print $2}' "$f")
      state=dead; kill -0 "$pid" 2>/dev/null && state=watching
      echo "armed ($state): $(grep -v '^pid=' "$f" | tr '\n' ' ')"
    done
    [ "$any" = 1 ] || echo "inject: nothing armed"
    last=$(ls -t "$NVF_INJECT_DIR"/*.log 2>/dev/null | head -1 || true)
    [ -n "$last" ] && echo "last log: $last" && tail -3 "$last"
    exit 0
    ;;
  *) usage ;;
esac

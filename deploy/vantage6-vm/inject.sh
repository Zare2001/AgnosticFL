#!/usr/bin/env bash
# E6 fault injection on the vantage6 VM (EXPERIMENTS.md E6, PLAN.md §8.5). One-shot: arm, wait
# for the start of hub round N at one site, wait S seconds, act, log, exit.
# Trigger: the node log line that the algorithm prints when training starts
#   "[Task <run_id>]: info > site=<site> org=<id> round=<N> n=<examples>"
# Usage:
#   bash deploy/vantage6-vm/inject.sh arm <site> <action> --round N [--after S] [--node NAME]
#   bash deploy/vantage6-vm/inject.sh disarm [<site>] | status
#   actions: kill       SIGKILL the site's algorithm container of round N (the training process);
#                       the run ends 'crashed', the node stays up
#            kill-node  SIGKILL the site's node container; the watchdog cron (start.sh) brings it back
#            restart    graceful restart of the site's node (docker stop, then start.sh)
#   --node NAME  node name if it differs from the site (island: isl-h3 for v6-h3)
# Logs and state: ~/flhub-runtime/vantage6/e6/inject-<site>.log (UTC times of arm, trigger,
# action, node back). The watcher measures the node's recovery for kill-node/restart.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
DIR="$HOME/flhub-runtime/vantage6/e6"
mkdir -p "$DIR"
now() { date -u +%Y-%m-%dT%H:%M:%S.%3NZ; }

node_up() { [ -n "$(docker ps -q --filter "name=^vantage6-$1-user$")" ]; }
connected() {  # last connection line of the node's current container says connected
  docker logs "vantage6-$1-user" 2>&1 | grep -E 'Connected to the /tasks|Disconnected from the server' | tail -1 \
    | grep -q 'Connected to the /tasks'
}
start_cmd() {  # how this node is (re)started: the standard start.sh, or the island script
  case "$1" in
    isl-*) echo "bash $REPO/deploy/vantage6-vm/island.sh nodes" ;;
    *) echo "flock $HOME/flhub-runtime/vantage6/watchdog.lock bash $REPO/deploy/vantage6-vm/start.sh" ;;  # same lock as the cron
  esac
}

watch() {  # background worker: watch <site> <node> <action> <round> <after>
  local site="$1" node="$2" action="$3" round="$4" after="$5" since line run pid
  local logf="$DIR/inject-$site.log"
  since="$(date +%s)"
  echo "$(now) armed site=$site node=$node action=$action round=$round after=${after}s" >> "$logf"
  while true; do
    if ! node_up "$node"; then sleep 1; continue; fi
    line=""
    exec 3< <(docker logs -f --since "$since" "vantage6-$node-user" 2>&1 | sed -u 's/\x1b\[[0-9;]*m//g')
    pid=$!
    while IFS= read -r l <&3; do
      if [[ "$l" =~ site=$site\ org=[0-9]+\ round=$round(\ |$) ]]; then line="$l"; break; fi
    done
    exec 3<&-
    kill "$pid" 2>/dev/null || true
    [ -n "$line" ] && break
    sleep 1   # node container went away before round N: re-attach to the next one
  done
  run="$(echo "$line" | sed -n 's/.*\[Task \([0-9]*\)\].*/\1/p')"
  echo "$(now) trigger site=$site round=$round run_id=$run" >> "$logf"
  sleep "$after"
  case "$action" in
    kill)
      if docker kill "vantage6-$node-run-$run" >/dev/null 2>&1; then
        echo "$(now) action kill: SIGKILL algorithm container vantage6-$node-run-$run" >> "$logf"
      else
        echo "$(now) action kill: container vantage6-$node-run-$run already gone (training finished)" >> "$logf"
      fi
      ;;
    kill-node)
      docker kill "vantage6-$node-user" >/dev/null
      echo "$(now) action kill-node: SIGKILL node container vantage6-$node-user" >> "$logf"
      ;;
    restart)
      docker stop "vantage6-$node-user" >/dev/null
      echo "$(now) action restart: node container vantage6-$node-user stopped; starting" >> "$logf"
      $(start_cmd "$node") >> "$logf" 2>&1 || echo "$(now) start command failed (watchdog retries)" >> "$logf"
      ;;
  esac
  if [ "$action" != kill ]; then   # wait for the node to be back (watchdog or start command)
    for _ in $(seq 1 600); do
      if node_up "$node" && connected "$node"; then echo "$(now) recovered: node $node connected again" >> "$logf"; break; fi
      sleep 1
    done
  fi
  echo "$(now) done" >> "$logf"
  rm -f "$DIR/inject-$site.pid"
}

case "${1:-}" in
  arm)
    site="${2:?site}"; action="${3:?action}"; shift 3
    round=""; after=0; node="$site"
    while [ $# -gt 0 ]; do
      case "$1" in
        --round) round="$2"; shift ;; --after) after="$2"; shift ;; --node) node="$2"; shift ;;
        *) echo "unknown option $1" >&2; exit 2 ;;
      esac
      shift
    done
    case "$action" in kill|kill-node|restart) ;; *) echo "action must be kill | kill-node | restart" >&2; exit 2 ;; esac
    [[ "$round" =~ ^[0-9]+$ ]] || { echo "--round N is required" >&2; exit 2; }
    [[ "$after" =~ ^[0-9]+([.][0-9]+)?$ ]] || { echo "--after must be seconds" >&2; exit 2; }
    node_up "$node" || { echo "node container vantage6-$node-user is not running" >&2; exit 1; }
    if [ -f "$DIR/inject-$site.pid" ] && kill -0 "$(cat "$DIR/inject-$site.pid")" 2>/dev/null; then
      echo "already armed for $site (disarm first)" >&2; exit 1
    fi
    setsid nohup bash "$0" _watch "$site" "$node" "$action" "$round" "$after" >/dev/null 2>&1 < /dev/null &
    echo $! > "$DIR/inject-$site.pid"
    sleep 0.5
    echo "ARMED $site $action at round $round +${after}s (node $node, watcher pid $(cat "$DIR/inject-$site.pid"), log $DIR/inject-$site.log)"
    ;;
  _watch) shift; watch "$@" ;;
  disarm)
    for f in "$DIR"/inject-${2:-*}.pid; do
      [ -f "$f" ] || continue
      pid="$(cat "$f")"
      kill -- "-$pid" 2>/dev/null || kill "$pid" 2>/dev/null || true
      rm -f "$f"
      s="$(basename "$f" .pid)"; s="${s#inject-}"
      echo "$(now) disarmed" >> "$DIR/inject-$s.log"
      echo "disarmed $s"
    done
    ;;
  status)
    found=0
    for f in "$DIR"/inject-*.pid; do
      [ -f "$f" ] || continue
      found=1; s="$(basename "$f" .pid)"; s="${s#inject-}"
      if kill -0 "$(cat "$f")" 2>/dev/null; then echo "$s ARMED: $(grep armed "$DIR/inject-$s.log" | tail -1)"
      else echo "$s stale pid file"; fi
    done
    [ "$found" = 1 ] || echo "no injection armed"
    ;;
  *) echo "usage: $0 arm <site> <kill|kill-node|restart> --round N [--after S] [--node NAME] | disarm [<site>] | status" >&2; exit 2 ;;
esac

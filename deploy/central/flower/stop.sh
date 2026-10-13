#!/usr/bin/env bash
# Stop the Flower server side (idempotent): adapter first (it ends the relay run), then SuperLink.
set -euo pipefail
. "$(dirname "$0")/common.sh"

descendants() { local c; for c in $(pgrep -P "$1"); do echo "$c"; descendants "$c"; done; }

stop_pid() {  # stop_pid <name> <pidfile> <grace seconds>; also ends leftover child processes
  local name="$1" pidf="$2" grace="$3" pid kids
  if ! pid_alive "$pidf"; then echo "$name: not running"; rm -f "$pidf"; return; fi
  pid="$(cat "$pidf")"
  kids="$(descendants "$pid")"
  kill "$pid"
  for _ in $(seq "$((grace * 2))"); do kill -0 "$pid" 2>/dev/null || break; sleep 0.5; done
  kill -0 "$pid" 2>/dev/null && kill -9 "$pid"
  for k in $kids; do kill "$k" 2>/dev/null || true; done
  rm -f "$pidf"
  echo "$name: stopped (pid $pid)"
}

stop_pid adapter "$RUNTIME/run/adapter.pid" 30
stop_pid superlink "$RUNTIME/run/superlink.pid" 15   # its SuperExec/ServerApp children too

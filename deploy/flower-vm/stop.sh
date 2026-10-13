#!/usr/bin/env bash
# Stop both hospital SuperNodes (idempotent), including their SuperExec/ClientApp children.
set -euo pipefail
. "$(dirname "$0")/common.sh"
descendants() { local c; for c in $(pgrep -P "$1"); do echo "$c"; descendants "$c"; done; }
rm -f "$RUNTIME/nodes.enabled"   # keeps the watchdog cron entry from restarting them

for entry in $NODES; do
  site="${entry%%:*}"
  pidf="$RUNTIME/run/supernode-$site.pid"
  if ! pid_alive "$pidf"; then
    echo "$site: not running"; rm -f "$pidf"; continue
  fi
  pid="$(cat "$pidf")"
  kids="$(descendants "$pid")"
  kill "$pid"
  for _ in $(seq 20); do kill -0 "$pid" 2>/dev/null || break; sleep 0.5; done
  kill -0 "$pid" 2>/dev/null && kill -9 "$pid"
  for k in $kids; do kill "$k" 2>/dev/null || true; done
  rm -f "$pidf"
  echo "$site: stopped (pid $pid)"
done

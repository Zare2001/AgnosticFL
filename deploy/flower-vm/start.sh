#!/usr/bin/env bash
# Start both hospital SuperNodes (standard flower-supernode) against the SuperLink (idempotent).
# The only hub-specific settings are the SuperLink address and the node config (site id, data file).
#   bash deploy/flower-vm/start.sh                              # central SuperLink
#   FLHUB_SUPERLINK=127.0.0.1:9092 bash deploy/flower-vm/start.sh   # island SuperLink
set -euo pipefail
. "$(dirname "$0")/common.sh"
mkdir -p "$RUNTIME/logs" "$RUNTIME/run"

for entry in $NODES; do
  site="${entry%%:*}"; port="${entry##*:}"
  pidf="$RUNTIME/run/supernode-$site.pid"
  if pid_alive "$pidf"; then
    echo "$site: already running (pid $(cat "$pidf"))"; continue
  fi
  [ -f "$DATA_DIR/$site.csv" ] || { echo "$site: missing $DATA_DIR/$site.csv, run install.sh" >&2; exit 1; }
  mkdir -p "$RUNTIME/$site"
  # supernode.sh runs the standard flower-supernode and restarts it if it ever exits.
  # One FLWR_HOME per SuperNode so the two nodes never unpack the same FAB concurrently.
  setsid nohup bash "$REPO/deploy/flower-vm/supernode.sh" "$site" "$port" \
      >> "$RUNTIME/logs/supernode-$site.log" 2>&1 < /dev/null &
  echo $! > "$pidf"
  echo "$site: started (pid $!, superlink $SUPERLINK, local runtime port $port)"
done

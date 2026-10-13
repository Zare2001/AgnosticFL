#!/usr/bin/env bash
# Start both hospital SuperNodes (standard flower-supernode) against the SuperLink (idempotent).
# The only hub-specific settings are the SuperLink address and the node config (site id, data file).
#   bash deploy/flower-vm/start.sh                              # central SuperLink
#   FLHUB_SUPERLINK=127.0.0.1:9092 bash deploy/flower-vm/start.sh   # island SuperLink
# Also enables the watchdog cron entry (install.sh), which re-runs this script every minute.
set -euo pipefail
. "$(dirname "$0")/common.sh"
mkdir -p "$RUNTIME/logs" "$RUNTIME/run"
echo "$SUPERLINK" > "$RUNTIME/nodes.enabled"
quiet="${FLHUB_WATCHDOG:-0}"

for entry in $NODES; do
  site="${entry%%:*}"; port="${entry##*:}"
  pidf="$RUNTIME/run/supernode-$site.pid"
  if pid_alive "$pidf"; then
    [ "$quiet" = 1 ] || echo "$site: already running (pid $(cat "$pidf"))"
    continue
  fi
  # stage-1 file <site>.csv, or at least one provisioned data variant <variant>-<site>.csv next to it
  have_data=0
  for f in "$DATA_DIR/$site.csv" "$DATA_DIR"/*-"$site".csv; do
    if [ -f "$f" ]; then have_data=1; break; fi
  done
  [ "$have_data" = 1 ] || { echo "$site: no data in $DATA_DIR (<site>.csv or <variant>-<site>.csv)" >&2; exit 1; }
  mkdir -p "$RUNTIME/$site"
  # supernode.sh runs the standard flower-supernode and restarts it if it ever exits.
  # One FLWR_HOME per SuperNode so the two nodes never unpack the same FAB concurrently.
  setsid nohup bash "$REPO/deploy/flower-vm/supernode.sh" "$site" "$port" \
      >> "$RUNTIME/logs/supernode-$site.log" 2>&1 < /dev/null &
  echo $! > "$pidf"
  echo "$(date -u +%FT%TZ) $site: started (pid $!, superlink $SUPERLINK, local runtime port $port)"
done

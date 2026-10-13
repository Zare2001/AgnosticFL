#!/usr/bin/env bash
# Stop both hospital SuperNodes (idempotent), including their SuperExec/ClientApp children.
set -euo pipefail
. "$(dirname "$0")/common.sh"
rm -f "$RUNTIME/nodes.enabled"   # keeps the watchdog cron entry from restarting them

for entry in $NODES; do
  stop_site "${entry%%:*}"
done

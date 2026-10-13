#!/usr/bin/env bash
# Stop the vantage6 nodes (idempotent). `v6 node stop` exits 1 on the root-owned log file
# (ADR-005), so the node containers are stopped directly; they are started with auto-remove.
set -euo pipefail
for s in v6-h3 v6-h4; do
  ids="$(docker ps -q --filter "name=^vantage6-$s-user$")"
  if [ -n "$ids" ]; then docker stop $ids >/dev/null; echo "[v6-vm-stop] stopped node $s"; fi
done

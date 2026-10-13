#!/usr/bin/env bash
# Hospital housekeeping (ops, ledger): vantage6 4.15.2 nodes never delete their per-run folders
# task-<run_id> (input + output, ~2.7x the model per site and round) in the node's data volume.
# Removes a node's task folders that are older than MIN_AGE minutes and whose algorithm container
# is not running (the node reads the output right after the container exits). Run every minute by
# the cron entry of install.sh; logs to ~/flhub-runtime/vantage6/taskdir_gc.log.
# Usage: bash deploy/vantage6-vm/taskdir_gc.sh [node ...]   (default: every vantage6 node volume)
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
. "$REPO/deploy/versions.env"
IMG="ghcr.io/vantage6/infrastructure/node:$VANTAGE6_VERSION"   # present on every node host
MIN_AGE="${V6_TASKDIR_MIN_AGE:-2}"
[ $# -gt 0 ] || set -- $(docker volume ls -q | sed -n 's/^vantage6-\(.*\)-user-vol$/\1/p')
for node in "$@"; do
  vol="vantage6-$node-user-vol"
  running=" $(docker ps --filter label=vantage6-type=algorithm --filter "label=node=$node" --format '{{.Label "run_id"}}' | tr '\n' ' ') "
  old="$(docker run --rm --network none -v "$vol:/v:ro" --entrypoint find "$IMG" /v -maxdepth 1 -type d \
         -name 'task-*' -mmin "+$MIN_AGE" -printf '%f\n')"
  drop=()
  for d in $old; do
    id=$((10#${d#task-}))
    case "$running" in *" $id "*) ;; *) drop+=("/v/$d") ;; esac
  done
  [ ${#drop[@]} -gt 0 ] || continue
  freed="$(docker run --rm --network none -v "$vol:/v" --entrypoint du "$IMG" -sc "${drop[@]}" | tail -1 | cut -f1)"
  docker run --rm --network none -v "$vol:/v" --entrypoint rm "$IMG" -rf -- "${drop[@]}"
  echo "$(date -u +%FT%TZ) $node: removed ${#drop[@]} task folders (${freed} KiB)"
done

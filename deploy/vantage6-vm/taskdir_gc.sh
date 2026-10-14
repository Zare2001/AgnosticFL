#!/usr/bin/env bash
# Hospital housekeeping (ops, ledger): vantage6 4.15.2 nodes never delete their per-run folders
# task-<run_id> (input + output, ~2.7x the model per site and round) in the node's data volume.
# Removes a node's task folders whose NEWEST FILE is older than MIN_AGE minutes and whose algorithm
# container is not running. The age is taken from the files, not from the folder: run ids are reused
# when tasks are deleted (sqlite), and a reused folder keeps its old mtime while the node overwrites
# input/output in place (2026-10-14 incident: a fresh output was removed before the node read it).
# Run every minute by the cron entry of install.sh; logs to ~/flhub-runtime/vantage6/taskdir_gc.log.
# Usage: [V6_TASKDIR_DRY=1] bash deploy/vantage6-vm/taskdir_gc.sh [node ...]   (default: every node volume)
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
. "$REPO/deploy/versions.env"
IMG="ghcr.io/vantage6/infrastructure/node:$VANTAGE6_VERSION"   # present on every node host
MIN_AGE="${V6_TASKDIR_MIN_AGE:-5}"
[ $# -gt 0 ] || set -- $(docker volume ls -q | sed -n 's/^vantage6-\(.*\)-user-vol$/\1/p')
now="$(date +%s)"
for node in "$@"; do
  vol="vantage6-$node-user-vol"
  running=" $(docker ps --filter label=vantage6-type=algorithm --filter "label=node=$node" --format '{{.Label "run_id"}}' | tr '\n' ' ') "
  # "<folder> <newest mtime>" for every task folder (empty folders: the folder's own mtime)
  ages="$(docker run --rm --network none -v "$vol:/v:ro" --entrypoint find "$IMG" /v -mindepth 1 -maxdepth 2 \
           -path '/v/task-*' -printf '%P %T@\n' \
         | awk '{split($1, p, "/"); d = p[1]; if (!(d in m) || $2 > m[d]) m[d] = $2} END {for (d in m) print d, int(m[d])}')"
  drop=()
  while read -r d t; do
    [ -n "${d:-}" ] || continue
    [ $((now - t)) -gt $((MIN_AGE * 60)) ] || continue
    id=$((10#${d#task-}))
    case "$running" in *" $id "*) ;; *) drop+=("/v/$d") ;; esac
  done <<< "$ages"
  [ ${#drop[@]} -gt 0 ] || continue
  if [ -n "${V6_TASKDIR_DRY:-}" ]; then echo "$node: would remove ${drop[*]}"; continue; fi
  freed="$(docker run --rm --network none -v "$vol:/v" --entrypoint du "$IMG" -sc "${drop[@]}" | tail -1 | cut -f1)"
  docker run --rm --network none -v "$vol:/v" --entrypoint rm "$IMG" -rf -- "${drop[@]}"
  echo "$(date -u +%FT%TZ) $node: removed ${#drop[@]} task folders (${freed} KiB)"
done

#!/usr/bin/env bash
# E6e: restart only the vantage6 server (the adapter keeps running). The server state lives in its
# sqlite DB, the nodes reconnect by themselves and the adapter waits out the outage, so a round in
# flight continues. Prints UTC times of stop, start and "API up" for the E6 table.
# Usage: bash deploy/central/vantage6/restart_server.sh [--down S]   (stay down S seconds, default 0)
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../../.." && pwd)"
NAME=flhub
down=0
[ "${1:-}" = "--down" ] && down="${2:?--down needs seconds}"
now() { date -u +%Y-%m-%dT%H:%M:%S.%3NZ; }
ids="$(docker ps -q --filter "label=vantage6-type=server" --filter "label=name=$NAME")"
echo "$(now) stopping vantage6 server ${ids:-<not running>}"
[ -z "$ids" ] || docker stop $ids >/dev/null
for _ in $(seq 1 30); do  # auto-remove: wait until the container name is free
  [ -z "$(docker ps -aq --filter "label=vantage6-type=server" --filter "label=name=$NAME")" ] && break
  sleep 0.5
done
echo "$(now) stopped"
sleep "$down"
echo "$(now) starting"
bash "$REPO/deploy/central/vantage6/start.sh" | grep 'server up'
echo "$(now) API up"

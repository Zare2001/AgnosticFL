#!/usr/bin/env bash
# Stop the vantage6 adapter and the vantage6 server on the central VM (idempotent).
set -euo pipefail
NAME=flhub
RT="$HOME/flhub-runtime/vantage6"
if [ -f "$RT/adapter.pid" ]; then
  pid="$(cat "$RT/adapter.pid")"
  if kill -0 "$pid" 2>/dev/null; then kill "$pid"; for _ in $(seq 1 20); do kill -0 "$pid" 2>/dev/null || break; sleep 0.5; done; fi
  kill -0 "$pid" 2>/dev/null && kill -9 "$pid"
  rm -f "$RT/adapter.pid"
fi
ids="$(docker ps -q --filter "label=vantage6-type=server" --filter "label=name=$NAME")"
if [ -n "$ids" ]; then docker stop $ids >/dev/null; fi
echo "[v6-stop] vantage6 adapter and server stopped"

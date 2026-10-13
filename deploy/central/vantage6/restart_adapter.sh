#!/usr/bin/env bash
# Restart ONLY the vantage6 adapter (conformance --restart-cmd; E6d after a SIGKILL just run start.sh,
# which starts a new adapter when the pid file is stale). The vantage6 server and the nodes are not
# touched; vantage6 tasks in flight survive (they live in the server).
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../../.." && pwd)"
RT="${V6_RT:-$HOME/flhub-runtime/vantage6}"
if [ -f "$RT/adapter.pid" ]; then
  pid="$(cat "$RT/adapter.pid")"
  if kill -0 "$pid" 2>/dev/null; then
    kill "$pid"
    for _ in $(seq 1 20); do kill -0 "$pid" 2>/dev/null || break; sleep 0.5; done
    kill -0 "$pid" 2>/dev/null && kill -9 "$pid"
  fi
  rm -f "$RT/adapter.pid"
fi
bash "$REPO/deploy/central/vantage6/start.sh" | grep -v 'server up'

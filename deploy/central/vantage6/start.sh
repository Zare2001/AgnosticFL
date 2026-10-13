#!/usr/bin/env bash
# Start the vantage6 server (Docker, :7601) and the vantage6 adapter (:8812) on the central VM.
# Idempotent: running parts are left alone. Needs install.sh first; the hub core (:8800) is central's.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../../.." && pwd)"
. "$REPO/deploy/topology.env"
. "$REPO/deploy/versions.env"
NAME=flhub
RT="${V6_RT:-$HOME/flhub-runtime/vantage6}"
SERVER_URL="$(grep '^V6_SERVER_URL=' "$RT/adapter.env" 2>/dev/null | cut -d= -f2- || true)"
SERVER_URL="${SERVER_URL:-http://127.0.0.1}"
VENV="${V6_VENV:-$HOME/venvs/v6}"
CFG="$HOME/.config/vantage6/server/$NAME.yaml"
V6_SERVER_IMAGE="${V6_SERVER_IMAGE:-ghcr.io/vantage6/infrastructure/server:$VANTAGE6_VERSION}"
[ -f "$RT/adapter.env" ] && [ -f "$CFG" ] || { echo "run deploy/central/vantage6/install.sh first" >&2; exit 1; }
log() { echo "[v6-start] $*"; }

# server: started with -c (no CLI file logging, ADR-005); stopped by Docker label in stop.sh
if [ -z "$(docker ps -q --filter "label=vantage6-type=server" --filter "label=name=$NAME")" ]; then
  (cd "$HOME" && "$VENV/bin/v6" server start -c "$CFG" --user --image "$V6_SERVER_IMAGE" >"$RT/server-start.log" 2>&1) \
    || { tail -20 "$RT/server-start.log" >&2; exit 1; }
fi
for _ in $(seq 1 60); do curl -sf "$SERVER_URL:$V6_SERVER_PORT/api/version" >/dev/null && break; sleep 1; done
curl -sf "$SERVER_URL:$V6_SERVER_PORT/api/version" >/dev/null || { echo "vantage6 server did not come up" >&2; exit 1; }
log "vantage6 server up: $(curl -s "$SERVER_URL:$V6_SERVER_PORT/api/version")"

# adapter: background process with a pid file; gets only its own password from secrets.env
if [ -f "$RT/adapter.pid" ] && [ "$RT/adapter.env" -nt "$RT/adapter.pid" ]; then
  log "adapter.env changed (new image?): restarting the adapter"
  kill "$(cat "$RT/adapter.pid")" 2>/dev/null || true; sleep 1; rm -f "$RT/adapter.pid"
fi
if [ -f "$RT/adapter.pid" ] && kill -0 "$(cat "$RT/adapter.pid")" 2>/dev/null; then
  log "adapter already running (pid $(cat "$RT/adapter.pid"))"
else
  pw="$(grep '^V6_ADAPTER_PASSWORD=' "$RT/secrets.env" | cut -d= -f2-)"
  (set -a; . "$RT/adapter.env"; set +a; cd "$REPO"
   V6_ADAPTER_PASSWORD="$pw" nohup "$VENV/bin/python" -m adapters.vantage6 --port "$ADAPTER_PORT" \
     >>"$RT/adapter.log" 2>&1 </dev/null &
   echo $! > "$RT/adapter.pid")
  for _ in $(seq 1 30); do curl -sf "http://127.0.0.1:$ADAPTER_V6_PORT/health" >/dev/null && break; sleep 1; done
  curl -sf "http://127.0.0.1:$ADAPTER_V6_PORT/health" >/dev/null || { tail -20 "$RT/adapter.log" >&2; exit 1; }
  log "adapter up on 127.0.0.1:$ADAPTER_V6_PORT (pid $(cat "$RT/adapter.pid"), log $RT/adapter.log)"
fi

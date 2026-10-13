#!/usr/bin/env bash
# Start the Flower server side on the central VM (idempotent):
#   1. SuperLink: Fleet API $FLEET_BIND (hospital SuperNodes), HTTP API 127.0.0.1:9093
#   2. Flower adapter on 127.0.0.1:8813; it starts the relay run (flwr run) within ~20 s.
set -euo pipefail
. "$(dirname "$0")/common.sh"
mkdir -p "$RUNTIME/logs" "$RUNTIME/run"
[ -f "$FLWR_HOME_DIR/config.toml" ] || { echo "run install.sh first" >&2; exit 1; }

wait_for() {  # wait_for <seconds> <description> <command...>
  local secs="$1" what="$2"; shift 2
  for _ in $(seq "$((secs * 2))"); do "$@" && return 0; sleep 0.5; done
  echo "timeout waiting for $what" >&2; return 1
}

if pid_alive "$RUNTIME/run/superlink.pid"; then
  echo "superlink: already running (pid $(cat "$RUNTIME/run/superlink.pid"))"
else
  # FAB dependencies are preinstalled in $VENV, so runtime dependency installation is off.
  # Persistent state: SuperNodes keep their node ids across a SuperLink restart (ADR).
  FLWR_HOME="$FLWR_HOME_DIR" setsid nohup flower-superlink --insecure \
      --fleet-api-address "$FLEET_BIND" --host 127.0.0.1 --port "$CONTROL_PORT" \
      --database "$RUNTIME/superlink.db" --disable-runtime-dependency-installation \
      >> "$RUNTIME/logs/superlink.log" 2>&1 < /dev/null &
  echo $! > "$RUNTIME/run/superlink.pid"
  wait_for 30 "SuperLink HTTP API :$CONTROL_PORT" port_open "$CONTROL_PORT"
  wait_for 30 "SuperLink Fleet API :${FLEET_BIND##*:}" port_open "${FLEET_BIND##*:}"
  echo "superlink: started (pid $!, fleet $FLEET_BIND, http 127.0.0.1:$CONTROL_PORT)"
fi

if pid_alive "$RUNTIME/run/adapter.pid"; then
  echo "adapter: already running (pid $(cat "$RUNTIME/run/adapter.pid"))"
else
  cd "$REPO"   # adapters.flower is imported from the repo checkout
  setsid nohup "$VENV/bin/python" -m adapters.flower.service \
      --port "$ADAPTER_PORT" --sites $SITES_FLOWER --hub "$HUB_URL" \
      --app-dir "$REPO/clients/flower" --flwr-home "$FLWR_HOME_DIR" --connection flhub \
      >> "$RUNTIME/logs/adapter.log" 2>&1 < /dev/null &
  echo $! > "$RUNTIME/run/adapter.pid"
  wait_for 20 "adapter :$ADAPTER_PORT" curl -sf "http://127.0.0.1:$ADAPTER_PORT/health" -o /dev/null
  sleep 1   # an older adapter still holding the port also answers /health: the new one must be alive
  pid_alive "$RUNTIME/run/adapter.pid" || { echo "adapter exited at start-up (port :$ADAPTER_PORT busy?)" >&2; exit 1; }
  echo "adapter: started (pid $(cat "$RUNTIME/run/adapter.pid"), http://127.0.0.1:$ADAPTER_PORT)"
fi
echo "relay run is started by the adapter; check: bash deploy/central/flower/status.sh"

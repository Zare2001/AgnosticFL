#!/usr/bin/env bash
# Flower island on the flower VM for S1 tests (never touches the hospital SuperNodes that
# serve central: separate runtime dir ~/flhub-runtime/island, SuperNode ports 19094/19095).
#   bash deploy/flower-vm/island.sh up|down|status
# up:   hub core 127.0.0.1:8800 + SuperLink (fleet 127.0.0.1:9092, HTTP API 9093) + adapter :8813
#       from ~/venvs/island (hub side), and SuperNodes flw-h5/flw-h6 from the hospital venv.
#   python -m hub.orchestrator run --config deploy/flower-vm/island-3r.yaml --wandb offline
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
ISLAND="${FLHUB_ISLAND_DIR:-$HOME/flhub-runtime/island}"
HUB_VENV="${FLHUB_ISLAND_VENV:-$HOME/venvs/island}"
server_env=(FLHUB_FLOWER_VENV="$HUB_VENV" FLHUB_FLOWER_RUNTIME="$ISLAND/server"
            FLOWER_FLEET_BIND=127.0.0.1:9092 FLHUB_HUB_URL=http://127.0.0.1:8800)
node_env=(FLHUB_FLOWER_RUNTIME="$ISLAND/nodes" FLHUB_SUPERLINK=127.0.0.1:9092
          FLHUB_FLOWER_NODES="flw-h5:19094 flw-h6:19095")
hub_pid="$ISLAND/hubcore.pid"
pid_alive() { [ -f "$1" ] && kill -0 "$(cat "$1")" 2>/dev/null; }

case "${1:-}" in
  up)
    mkdir -p "$ISLAND"
    if ! pid_alive "$hub_pid"; then
      cd "$REPO"
      setsid nohup "$HUB_VENV/bin/python" -m hub.core --port 8800 --root "$ISLAND/hubcore" \
          >> "$ISLAND/hubcore.log" 2>&1 < /dev/null &
      echo $! > "$hub_pid"
      for _ in $(seq 40); do curl -sf http://127.0.0.1:8800/health -o /dev/null && break; sleep 0.5; done
    fi
    echo "hub core: pid $(cat "$hub_pid")"
    [ -f "$ISLAND/server/flwr-home/config.toml" ] || env "${server_env[@]}" bash "$REPO/deploy/central/flower/install.sh" >/dev/null
    env "${server_env[@]}" bash "$REPO/deploy/central/flower/start.sh"
    env "${node_env[@]}" bash "$REPO/deploy/flower-vm/start.sh"
    ;;
  down)
    env "${node_env[@]}" bash "$REPO/deploy/flower-vm/stop.sh" || true
    env "${server_env[@]}" bash "$REPO/deploy/central/flower/stop.sh" || true
    if pid_alive "$hub_pid"; then kill "$(cat "$hub_pid")"; echo "hub core: stopped"; fi
    rm -f "$hub_pid"
    ;;
  status)
    pid_alive "$hub_pid" && echo "hub core: up (pid $(cat "$hub_pid"))" || echo "hub core: down"
    env "${server_env[@]}" bash "$REPO/deploy/central/flower/status.sh" || true
    env "${node_env[@]}" bash "$REPO/deploy/flower-vm/status.sh" || true
    ;;
  *) echo "usage: $0 up|down|status" >&2; exit 2 ;;
esac

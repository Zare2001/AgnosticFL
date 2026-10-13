#!/usr/bin/env bash
# Development island on the vantage6 VM (never used by final runs): hub core, registry, vantage6
# server and adapter (the deploy/central/vantage6 scripts with V6_RT/V6_VENV/V6_SERVER_BIND
# overrides) plus two island nodes isl-h3/isl-h4 that are organisations v6-h3/v6-h4 of the
# island server. The real hospital nodes v6-h3/v6-h4 stay connected to central untouched.
# The island server port is published on the docker0 address only (not on the public NIC).
# Usage: bash deploy/vantage6-vm/island.sh {up|down|status|nodes} [--reimport] [--skip-image]
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
. "$REPO/deploy/topology.env"
. "$REPO/deploy/versions.env"
export V6_RT="$HOME/flhub-runtime/v6-island" V6_VENV="$HOME/venvs/island"
export V6_SERVER_BIND="${V6_SERVER_BIND:-172.17.0.1}"
NODES="isl-h3:v6-h3 isl-h4:v6-h4"     # island node name : organisation (= site id)
NODE_VENV="$HOME/venvs/v6"            # the standard node CLI
V6_NODE_IMAGE="${V6_NODE_IMAGE:-ghcr.io/vantage6/infrastructure/node:$VANTAGE6_VERSION}"
log() { echo "[v6-island] $*"; }

hubcore_up() {
  curl -sf "http://127.0.0.1:$HUB_CORE_PORT/health" >/dev/null && return 0
  (FLHUB_HUBCORE_ROOT="$V6_RT/hubcore" nohup "$V6_VENV/bin/python" -m hub.core --port "$HUB_CORE_PORT" \
     >>"$V6_RT/hubcore.log" 2>&1 </dev/null & echo $! > "$V6_RT/hubcore.pid")
  for _ in $(seq 1 20); do curl -sf "http://127.0.0.1:$HUB_CORE_PORT/health" >/dev/null && return 0; sleep 0.5; done
  echo "hub core did not come up, see $V6_RT/hubcore.log" >&2; return 1
}

nodes_up() {
  set -a; . "$V6_RT/v6-nodes.env"; set +a
  local allowed extra="node_extra_hosts:"$'\n'"  host.docker.internal: host-gateway"
  allowed="^localhost:$REGISTRY_PORT/flhub/v6-train(:[A-Za-z0-9._-]+)?(@sha256:[a-f0-9]{64})?\$"
  for pair in $NODES; do
    local node="${pair%%:*}" site="${pair##*:}" var
    var="V6_API_KEY_$(echo "$site" | tr 'a-z-' 'A-Z_')"
    (umask 077; V6_API_KEY="${!var}" V6_SERVER_URL="http://host.docker.internal" V6_SERVER_PORT="$V6_SERVER_PORT" \
      V6_TASK_DIR="$HOME/.local/share/vantage6/node/$node" V6_DATA_FILE="$HOME/flhub-data/$site.csv" V6_DATA_TYPE=csv \
      V6_NODE_IMAGE="$V6_NODE_IMAGE" V6_ALLOWED_ALGORITHMS="$allowed" V6_SITE="$node" V6_EXTRA_CONFIG="$extra" \
      envsubst '${V6_API_KEY} ${V6_SERVER_URL} ${V6_SERVER_PORT} ${V6_TASK_DIR} ${V6_DATA_FILE} ${V6_DATA_TYPE} ${V6_NODE_IMAGE} ${V6_ALLOWED_ALGORITHMS} ${V6_SITE} ${V6_EXTRA_CONFIG}' \
      < "$REPO/deploy/vantage6-vm/node.template.yaml" > "$HOME/.config/vantage6/node/$node.yaml")
    if [ -z "$(docker ps -q --filter "name=^vantage6-$node-user$")" ]; then
      (cd "$HOME" && "$NODE_VENV/bin/v6" node start -n "$node" --user >/dev/null)
      log "started island node $node (organisation $site)"
    fi
  done
  for pair in $NODES; do
    local node="${pair%%:*}" last=""
    for _ in $(seq 1 90); do
      last="$(docker logs "vantage6-$node-user" 2>&1 | grep -E 'Websocket connection established|Connected to the /tasks|Disconnected from the server' | tail -1 || true)"
      case "$last" in *Disconnected*|"") sleep 1 ;; *) break ;; esac
    done
    case "$last" in *Disconnected*|"") echo "island node $node not connected" >&2; return 1 ;; esac
    log "island node $node connected"
  done
}

case "${1:-}" in
  up)
    shift
    [ -x "$V6_VENV/bin/python" ] || { echo "no island venv $V6_VENV (see docs/agents/worklogs/stage2-vantage6.md)" >&2; exit 1; }
    mkdir -p "$V6_RT"; chmod 700 "$V6_RT"
    bash "$REPO/deploy/central/vantage6/install.sh" --island "$@"
    hubcore_up
    bash "$REPO/deploy/central/vantage6/start.sh"
    nodes_up
    bash "$0" status
    ;;
  down)
    for pair in $NODES; do
      ids="$(docker ps -q --filter "name=^vantage6-${pair%%:*}-user$")"
      [ -z "$ids" ] || docker stop $ids >/dev/null
    done
    bash "$REPO/deploy/central/vantage6/stop.sh"
    if [ -f "$V6_RT/hubcore.pid" ]; then kill "$(cat "$V6_RT/hubcore.pid")" 2>/dev/null || true; rm -f "$V6_RT/hubcore.pid"; fi
    ids="$(docker ps -q --filter name=^flhub-registry$)"; [ -z "$ids" ] || docker stop $ids >/dev/null
    log "island stopped (registry container stopped, data kept in $V6_RT)"
    ;;
  nodes) nodes_up ;;   # (re)start only the island nodes (inject.sh restart)
  status)
    bash "$REPO/deploy/central/vantage6/status.sh" || true
    for pair in $NODES; do
      node="${pair%%:*}"
      if [ -n "$(docker ps -q --filter "name=^vantage6-$node-user$")" ]; then echo "$node   UP    (organisation ${pair##*:})"
      else echo "$node   DOWN"; fi
    done
    ;;
  *) echo "usage: $0 {up|down|status|nodes} [--reimport] [--skip-image]" >&2; exit 2 ;;
esac

#!/usr/bin/env bash
# E7 native FedAvg for vantage6 on central (EXPERIMENTS.md E7):
#  1. native image = the hub's algorithm image (adapter.env) + v6_native, tagged <hub tag>-native
#     in the same repository (the hospitals' image policy admits it) and pushed to the registry;
#  2. the node of organisation hub-operator (the hub's own collaboration member) is started next to
#     the server: it runs the central function; no data, no hospital change;
#  3. python -m adapters.vantage6.native run: one task, central function fedavg, hub-format run dir;
#  4. the hub-operator node is stopped again (also on failure).
# Usage: bash deploy/central/vantage6/native/run.sh <hub config.yaml> <out_dir> [--rounds N]
#   e.g. configs/stage2/e7/s2-e7-vantage6-hub-cnn.yaml ~/flhub-runtime/runs/s2-e7-vantage6-native-cnn
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../../../.." && pwd)"
. "$REPO/deploy/topology.env"
. "$REPO/deploy/versions.env"
RT="${V6_RT:-$HOME/flhub-runtime/vantage6}"
VENV="${V6_VENV:-$HOME/venvs/v6}"
NODE=flhub-operator
CONFIG="${1:?hub config yaml}"; OUT="${2:?out dir}"; shift 2
log() { echo "[v6-native] $(date -u +%H:%M:%SZ) $*"; }
set -a; . "$RT/adapter.env"; set +a          # V6_ALGO_IMAGE, V6_SERVER_URL, V6_SERVER_PORT
LOCAL="localhost:$REGISTRY_PORT/${V6_ALGO_IMAGE#*/}"    # the hub image under the local registry name
NATIVE="$V6_ALGO_IMAGE-native"; NATIVE_LOCAL="$LOCAL-native"

# --- 1. native image ---
docker image inspect "$LOCAL" >/dev/null 2>&1 || docker pull -q "$LOCAL" >/dev/null
docker build -q --build-arg BASE="$LOCAL" -t "$NATIVE_LOCAL" "$REPO/deploy/central/vantage6/native" >/dev/null
docker push -q "$NATIVE_LOCAL" >/dev/null
[ "$NATIVE" = "$NATIVE_LOCAL" ] || docker tag "$NATIVE_LOCAL" "$NATIVE"   # local copy for the hub-operator node
log "native image $NATIVE (base $V6_ALGO_IMAGE)"

# --- 2. hub-operator node (vantage6 node of the hub's organisation) ---
key="$(grep '^V6_API_KEY_HUB=' "$RT/secrets.env" | cut -d= -f2-)"
mkdir -p "$HOME/.config/vantage6/node"
allowed="^$(echo "${V6_ALGO_IMAGE%%/*}" | sed 's/\./\\./g')/flhub/v6-train:[A-Za-z0-9._-]+-native\$"
(umask 077; cat > "$HOME/.config/vantage6/node/$NODE.yaml" <<EOF
api_key: $key
api_path: /api
server_url: http://host.docker.internal
port: $V6_SERVER_PORT
task_dir: $HOME/.local/share/vantage6/node/$NODE
databases: []
encryption:
  enabled: false
  private_key: null
images:
  node: ghcr.io/vantage6/infrastructure/node:$VANTAGE6_VERSION
node_extra_hosts:
  host.docker.internal: host-gateway
policies:
  allowed_algorithms:
  - '$allowed'
  require_algorithm_pull: false
share_config: false
logging:
  backup_count: 5
  datefmt: '%Y-%m-%d %H:%M:%S'
  file: $NODE.log
  format: '%(asctime)s - %(name)-14s - %(levelname)-8s - %(message)s'
  level: INFO
  max_size: 1024
  use_console: true
  loggers:
  - {level: warning, name: urllib3}
  - {level: warning, name: requests}
  - {level: warning, name: engineio.client}
EOF
)
stop_node() { ids="$(docker ps -q --filter "name=^vantage6-$NODE-user$")"; [ -z "$ids" ] || docker stop $ids >/dev/null; log "hub-operator node stopped"; }
trap stop_node EXIT
if [ -z "$(docker ps -q --filter "name=^vantage6-$NODE-user$")" ]; then
  (cd "$HOME" && "$VENV/bin/v6" node start -n "$NODE" --user >/dev/null)
fi
for _ in $(seq 1 90); do
  last="$(docker logs "vantage6-$NODE-user" 2>&1 | grep -E 'Connected to the /tasks|Disconnected from the server' | tail -1 || true)"
  case "$last" in *Connected*) break ;; *) sleep 1 ;; esac
done
case "$last" in *Connected*) log "hub-operator node connected" ;; *) echo "hub-operator node not connected" >&2; exit 1 ;; esac

# --- 3. the native run ---
pw="$(grep '^V6_ADAPTER_PASSWORD=' "$RT/secrets.env" | cut -d= -f2-)"
(cd "$REPO" && V6_ADAPTER_PASSWORD="$pw" "$VENV/bin/python" -m adapters.vantage6.native run --config "$CONFIG" \
   --out "$OUT" --image "$NATIVE" --server-url "$V6_SERVER_URL" --server-port "$V6_SERVER_PORT" "$@")

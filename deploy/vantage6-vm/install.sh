#!/usr/bin/env bash
# Install the vantage6 hospital nodes v6-h3 and v6-h4 on the vantage6 VM (idempotent).
# A hospital installs only the standard vantage6 node (vantage6 CLI + Docker). Hub-specific are
# only: the server address, the node API keys (from the operator) and the allowed algorithm image.
# Usage: bash deploy/vantage6-vm/install.sh [--keys FILE] [--island]
#   --keys FILE  node API keys file from the operator (tools/bus get <id> -o FILE); it is moved
#                to ~/flhub-runtime/vantage6/node-keys.env (mode 600)
#   --island     P1 island: server on this VM (host.docker.internal:7601), image from localhost:5050
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
. "$REPO/deploy/topology.env"
. "$REPO/deploy/versions.env"
SITES="v6-h3 v6-h4"
RT="$HOME/flhub-runtime/vantage6"
VENV="$HOME/venvs/v6"
DATA_VENV="$HOME/venvs/flhub-data"
KEYS="$RT/node-keys.env"
V6_NODE_IMAGE="${V6_NODE_IMAGE:-ghcr.io/vantage6/infrastructure/node:$VANTAGE6_VERSION}"
ISLAND=0; KEYS_IN=""
while [ $# -gt 0 ]; do
  case "$1" in
    --island) ISLAND=1 ;;
    --keys) KEYS_IN="$2"; shift ;;
    *) echo "unknown option $1" >&2; exit 2 ;;
  esac
  shift
done
log() { echo "[v6-vm-install] $*"; }
command -v envsubst >/dev/null || sudo apt-get install -y -q gettext-base >/dev/null
mkdir -p "$RT"; chmod 700 "$HOME/flhub-runtime" "$RT"

# --- 1. the standard vantage6 node CLI ---
if [ ! -x "$VENV/bin/python" ]; then uv venv --python "$PYTHON_VERSION" "$VENV"; fi
uv pip install -q --python "$VENV/bin/python" "vantage6==$VANTAGE6_VERSION"
docker pull -q "$V6_NODE_IMAGE" >/dev/null
log "vantage6 node CLI $VANTAGE6_VERSION in $VENV, node image $V6_NODE_IMAGE"

# --- 2. the operator's registry is plain HTTP: allow it in the Docker daemon ---
if [ "$ISLAND" = 1 ]; then
  REGISTRY="localhost:$REGISTRY_PORT"; SERVER_URL="http://host.docker.internal"
  EXTRA=$'node_extra_hosts:\n  host.docker.internal: host-gateway'
else
  REGISTRY="$FLHUB_CENTRAL_IP:$REGISTRY_PORT"; SERVER_URL="http://$FLHUB_CENTRAL_IP"; EXTRA=""
  changed="$(sudo python3 - "$REGISTRY" <<'PY'
import json, os, sys
path, reg = "/etc/docker/daemon.json", sys.argv[1]
cfg = json.load(open(path)) if os.path.exists(path) and os.path.getsize(path) else {}
if reg in cfg.get("insecure-registries", []):
    print("no")
else:
    cfg.setdefault("insecure-registries", []).append(reg)
    with open(path, "w") as f:
        json.dump(cfg, f, indent=2)
    print("yes")
PY
)"
  if [ "$changed" = yes ]; then
    log "added $REGISTRY to insecure-registries; restarting Docker (nodes restart with start.sh)"
    sudo systemctl restart docker
  fi
fi

# --- 3. hospital data (simulation only: a real hospital already has its file) ---
if [ ! -x "$DATA_VENV/bin/python" ]; then uv venv -q --python "$PYTHON_VERSION" "$DATA_VENV"; fi
uv pip install -q --python "$DATA_VENV/bin/python" "numpy==$NUMPY_VERSION"
uv pip install -q --python "$DATA_VENV/bin/python" --no-deps "$REPO/flhub_core"   # data module needs numpy only
for s in $SITES; do
  if [ ! -f "$HOME/flhub-data/$s.csv" ]; then "$DATA_VENV/bin/python" -m flhub_core.data materialize --sites "$s"; fi
done
"$DATA_VENV/bin/python" - $SITES <<'PY'
import hashlib, sys
from flhub_core.data import PARTITION_SHA256, site_path
for s in sys.argv[1:]:
    got = hashlib.sha256(site_path(s).read_bytes()).hexdigest()
    assert got == PARTITION_SHA256[s], f"{s}: data file sha256 {got} != pinned {PARTITION_SHA256[s]}"
    print(f"[v6-vm-install] data {site_path(s)} sha256 ok")
PY

# --- 4. node API keys (from the operator, never in git) ---
if [ -n "$KEYS_IN" ]; then install -m 600 "$KEYS_IN" "$KEYS" && rm -f "$KEYS_IN"; fi
[ -f "$KEYS" ] || { echo "no node API keys: pass --keys FILE (from the operator via tools/bus)" >&2; exit 1; }
set -a; . "$KEYS"; set +a

# --- 5. one standard node config per hospital (`v6 node new` is interactive only) ---
mkdir -p "$HOME/.config/vantage6/node"
ALLOWED="^$(echo "$REGISTRY" | sed 's/\./\\./g')/flhub/v6-train(:[A-Za-z0-9._-]+)?(@sha256:[a-f0-9]{64})?\$"
for s in $SITES; do
  var="V6_API_KEY_$(echo "$s" | tr 'a-z-' 'A-Z_')"
  [ -n "${!var:-}" ] || { echo "$KEYS has no $var" >&2; exit 1; }
  (umask 077; V6_API_KEY="${!var}" V6_SERVER_URL="$SERVER_URL" V6_SERVER_PORT="$V6_SERVER_PORT" \
    V6_TASK_DIR="$HOME/.local/share/vantage6/node/$s" V6_DATA_FILE="$HOME/flhub-data/$s.csv" V6_DATA_TYPE=csv \
    V6_NODE_IMAGE="$V6_NODE_IMAGE" V6_ALLOWED_ALGORITHMS="$ALLOWED" V6_SITE="$s" V6_EXTRA_CONFIG="$EXTRA" \
    envsubst '${V6_API_KEY} ${V6_SERVER_URL} ${V6_SERVER_PORT} ${V6_TASK_DIR} ${V6_DATA_FILE} ${V6_DATA_TYPE} ${V6_NODE_IMAGE} ${V6_ALLOWED_ALGORITHMS} ${V6_SITE} ${V6_EXTRA_CONFIG}' \
    < "$REPO/deploy/vantage6-vm/node.template.yaml" > "$HOME/.config/vantage6/node/$s.yaml")
  log "node config ~/.config/vantage6/node/$s.yaml -> $SERVER_URL:$V6_SERVER_PORT"
done
echo "$([ "$ISLAND" = 1 ] && echo island || echo central)" > "$RT/node-mode"

# --- 6. watchdog: re-run start.sh every minute while the nodes are enabled (start.sh/stop.sh) ---
CRON="* * * * * [ -f $RT/nodes.enabled ] && flock -n $RT/watchdog.lock bash $REPO/deploy/vantage6-vm/start.sh >> $RT/watchdog.log 2>&1"
others="$(crontab -l 2>/dev/null | grep -v 'deploy/vantage6-vm/start.sh' || true)"
printf '%s\n%s\n' "$others" "$CRON" | sed '/^$/d' | crontab -
log "watchdog cron entry installed (log $RT/watchdog.log)"
log "done; allowed algorithm images: $ALLOWED"

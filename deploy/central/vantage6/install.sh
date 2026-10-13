#!/usr/bin/env bash
# Install the vantage6 side of the hub on the central VM (idempotent):
#   ~/venvs/v6 (vantage6 CLI/client + adapter deps), generated secrets in ~/flhub-runtime/vantage6/,
#   server config, entities import, algorithm image pushed to the registry on :5050.
# Usage: bash deploy/central/vantage6/install.sh [--island] [--reimport] [--skip-image]
#   --island      P1 island on a framework VM: run a local registry on 127.0.0.1:5050 and
#                 let the nodes pull localhost:5050/... (central: the registry is run by central)
#   --reimport    wipe the vantage6 server database and import the entities again
#   --skip-image  do not build/push the algorithm image
# Outputs: ~/flhub-runtime/vantage6/{secrets.env, adapter.env, v6-nodes.env (send to the vantage6 agent)}
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../../.." && pwd)"
. "$REPO/deploy/topology.env"
. "$REPO/deploy/versions.env"
ISLAND=0; REIMPORT=0; SKIP_IMAGE=0
for arg in "$@"; do
  case "$arg" in
    --island) ISLAND=1 ;; --reimport) REIMPORT=1 ;; --skip-image) SKIP_IMAGE=1 ;;
    *) echo "unknown option $arg" >&2; exit 2 ;;
  esac
done

NAME=flhub
RT="${V6_RT:-$HOME/flhub-runtime/vantage6}"
VENV="${V6_VENV:-$HOME/venvs/v6}"
DB_DIR="$HOME/.local/share/vantage6/server/$NAME"
. "$REPO/deploy/central/vantage6/common.sh"
SERVER_HOST="$([ "$V6_SERVER_BIND" = 0.0.0.0 ] && echo 127.0.0.1 || echo "$V6_SERVER_BIND")"
V6_BASE_SITES="v6-h3 v6-h4"
V6_EXTRA_SITES="${V6_EXTRA_SITES-v6-h9 v6-h10}"   # E5 sites: registered (add_sites.sh), offline outside E5
log() { echo "[v6-install] $*"; }
command -v envsubst >/dev/null || sudo apt-get install -y -q gettext-base >/dev/null

# --- 1. venv: vantage6 CLI + client, flhub_core and the hub package (adapters.common) ---
if [ ! -x "$VENV/bin/python" ]; then uv venv --python "$PYTHON_VERSION" "$VENV"; fi
uv pip install -q --python "$VENV/bin/python" "torch==${TORCH_VERSION}+cpu" --index-url "$TORCH_INDEX_URL"
uv pip install -q --python "$VENV/bin/python" "vantage6==$VANTAGE6_VERSION" "vantage6-client==$VANTAGE6_VERSION" \
  "numpy==$NUMPY_VERSION" "safetensors==$SAFETENSORS_VERSION" "pyyaml==$PYYAML_VERSION" \
  -e "$REPO/flhub_core" -e "$REPO"
log "venv $VENV ready (vantage6 $("$VENV/bin/python" -c 'from importlib.metadata import version; print(version("vantage6"))'))"

# --- 2. secrets (generated once, never in git) ---
mkdir -p "$RT"; chmod 700 "$HOME/flhub-runtime" "$RT"
if [ ! -f "$RT/secrets.env" ]; then
  (umask 077; python3 - > "$RT/secrets.env" <<'PY'
import secrets, uuid
print(f"V6_JWT_SECRET={secrets.token_hex(32)}")
print(f"V6_ROOT_PASSWORD=Rt{secrets.token_urlsafe(24)}7!")
print(f"V6_ADAPTER_PASSWORD=Fl{secrets.token_urlsafe(24)}9!")
for k in ("HUB", "V6_H3", "V6_H4"):
    print(f"V6_API_KEY_{k}={uuid.uuid4()}")
PY
  )
  log "generated $RT/secrets.env"
fi
chmod 600 "$RT/secrets.env"
set -a; . "$RT/secrets.env"; set +a

# --- 3. server config ---
render_server_config
docker pull -q "$V6_SERVER_IMAGE" >/dev/null
log "server config $CFG (image $V6_SERVER_IMAGE)"

# --- 4. entities import (once). `v6 server import` loses the DB on Python 3.11 (ADR-005),
#        so run the server image's own import command with VANTAGE6_DB_URI set. ---
server_container() { docker ps -q --filter "label=vantage6-type=server" --filter "label=name=$NAME"; }
if [ "$REIMPORT" = 1 ] || [ ! -f "$RT/.imported" ]; then
  if [ -n "$(server_container)" ]; then docker stop $(server_container) >/dev/null; fi
  mkdir -p "$DB_DIR"
  docker run --rm -v "$DB_DIR:/d" alpine:3 rm -f /d/$NAME.sqlite   # db file is owned by root
  (umask 077; envsubst '${V6_ROOT_PASSWORD} ${V6_ADAPTER_PASSWORD} ${V6_API_KEY_HUB} ${V6_API_KEY_V6_H3} ${V6_API_KEY_V6_H4}' \
    < "$REPO/deploy/central/vantage6/entities.template.yaml" > "$RT/entities.yaml")
  docker run --rm -v "$CFG:/mnt/config.yaml:ro" -v "$RT/entities.yaml:/mnt/import.yaml:ro" \
    -v "$DB_DIR:/mnt/database" -e VANTAGE6_DB_URI="sqlite:////mnt/database/$NAME.sqlite" \
    "$V6_SERVER_IMAGE" vserver-local import -c /mnt/config.yaml /mnt/import.yaml > "$RT/import.log" 2>&1
  rm -f "$RT/entities.yaml"
  grep -q "Create collaborations" "$RT/import.log" || { echo "entities import failed, see $RT/import.log" >&2; exit 1; }
  touch "$RT/.imported"
  log "entities imported (orgs hub-operator, v6-h3, v6-h4; collaboration flhub)"
else
  log "entities already imported (use --reimport to redo)"
fi

# --- 5. registry and algorithm image ---
if [ "$ISLAND" = 1 ]; then
  if [ -z "$(docker ps -q --filter name=^flhub-registry$)" ]; then
    if [ -n "$(docker ps -aq --filter name=^flhub-registry$)" ]; then
      docker start flhub-registry >/dev/null   # keep its volume (the pushed images)
    else
      docker run -d --restart=always --name flhub-registry -p 127.0.0.1:${REGISTRY_PORT}:5000 "$REGISTRY_IMAGE" >/dev/null
    fi
  fi
  PULL_REGISTRY="localhost:$REGISTRY_PORT"
else
  PULL_REGISTRY="$FLHUB_CENTRAL_IP:$REGISTRY_PORT"     # what the nodes pull (same registry)
fi
for _ in $(seq 1 20); do curl -sf "http://127.0.0.1:$REGISTRY_PORT/v2/" >/dev/null && break; sleep 1; done
curl -sf "http://127.0.0.1:$REGISTRY_PORT/v2/" >/dev/null || { echo "no Docker registry on 127.0.0.1:$REGISTRY_PORT" >&2; exit 1; }

TAG="$(git -C "$REPO" rev-parse --short=12 HEAD)"
if [ -n "$(git -C "$REPO" status --porcelain -- flhub_core clients/vantage6)" ]; then TAG="$TAG-dirty"; fi
if [ "$SKIP_IMAGE" = 1 ] && [ -f "$RT/adapter.env" ]; then   # keep the image that was pushed before
  TAG="$(grep '^V6_ALGO_IMAGE=' "$RT/adapter.env" | sed 's/.*://')"
fi
PUSH_IMAGE="localhost:$REGISTRY_PORT/flhub/v6-train:$TAG"
if [ "$SKIP_IMAGE" = 0 ]; then
  docker build -q -f "$REPO/clients/vantage6/Dockerfile" \
    --build-arg BASE_IMAGE="$V6_ALGO_BASE_IMAGE" --build-arg TORCH_VERSION="$TORCH_VERSION" \
    --build-arg TORCH_INDEX_URL="$TORCH_INDEX_URL" --build-arg NUMPY_VERSION="$NUMPY_VERSION" \
    --build-arg SAFETENSORS_VERSION="$SAFETENSORS_VERSION" --build-arg VANTAGE6_VERSION="$VANTAGE6_VERSION" \
    -t "$PUSH_IMAGE" "$REPO" >/dev/null
  docker push -q "$PUSH_IMAGE" >/dev/null
  log "algorithm image pushed: $PUSH_IMAGE (nodes pull $PULL_REGISTRY/flhub/v6-train:$TAG)"
fi

# --- 6. adapter settings (no secrets) and the node credentials for the vantage6 agent ---
cat > "$RT/adapter.env.new" <<EOF
V6_SERVER_URL=http://$SERVER_HOST
V6_SERVER_PORT=$V6_SERVER_PORT
V6_API_PATH=/api
V6_COLLABORATION=flhub
V6_SITES="$V6_BASE_SITES $V6_EXTRA_SITES"
V6_ALGO_IMAGE=$PULL_REGISTRY/flhub/v6-train:$TAG
ADAPTER_PORT=$ADAPTER_V6_PORT
EOF
# replace only on change: start.sh restarts the adapter when adapter.env is newer than its pid file
if cmp -s "$RT/adapter.env.new" "$RT/adapter.env"; then rm "$RT/adapter.env.new"; else mv "$RT/adapter.env.new" "$RT/adapter.env"; fi
(umask 077; cat > "$RT/v6-nodes.env" <<EOF
V6_API_KEY_V6_H3=$V6_API_KEY_V6_H3
V6_API_KEY_V6_H4=$V6_API_KEY_V6_H4
EOF
)
log "done. adapter image: $PULL_REGISTRY/flhub/v6-train:$TAG"
log "node credentials for the vantage6 VM: $RT/v6-nodes.env  (tools/bus put $RT/v6-nodes.env --to vantage6 --secret)"

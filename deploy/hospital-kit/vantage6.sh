# shellcheck shell=bash
# vantage6 hospital (sourced as framework.sh): the standard vantage6 node (Docker), with the API key
# the operator issued for this site (credentials/api_key). Hub-specific are only the server address,
# the key and the one algorithm image the node may run; the training code (flhub_core) is inside
# that image, which the node pulls from the operator's registry.
NODE_CFG="$HOME/.config/vantage6/node/$SITE.yaml"
CONTAINER="vantage6-$SITE-user"
REGISTRY="$CENTRAL_IP:$REGISTRY_PORT"

db_label() { echo "$1" | sed 's/[^A-Za-z0-9_]/_/g'; }  # = adapters.vantage6 db_label (ADR-014)

node_databases() {  # one CSV database per data file: <site>.csv as `default`, <variant>-<site>.csv by variant
  local f v
  [ -f "$DATA_DIR/$SITE.csv" ] && printf -- '- label: default\n  uri: %s\n  type: csv\n' "$DATA_DIR/$SITE.csv"
  for f in "$DATA_DIR"/*-"$SITE".csv; do
    [ -f "$f" ] || continue
    v=$(basename "$f" "-$SITE.csv")
    printf -- '- label: %s\n  uri: %s\n  type: csv\n' "$(db_label "$v")" "$f"
  done
  return 0
}

render_config() {  # ~/.config/vantage6/node/<site>.yaml (mode 600) from node.template.yaml and the data files
  local allowed dbs
  allowed="^$(echo "$REGISTRY" | sed 's/\./\\./g')/flhub/v6-train(:[A-Za-z0-9._-]+)?(@sha256:[a-f0-9]{64})?\$"
  dbs=$(node_databases)
  [ -n "$dbs" ] || dbs="  []"
  mkdir -p "$(dirname "$NODE_CFG")"
  (umask 077
   V6_API_KEY="$(cat "$KIT/credentials/api_key")" V6_SERVER_URL="http://$CENTRAL_IP" V6_SERVER_PORT="$V6_SERVER_PORT" \
   V6_TASK_DIR="$HOME/.local/share/vantage6/node/$SITE" V6_DATABASES="$dbs" V6_NODE_IMAGE="$V6_NODE_IMAGE" \
   V6_ALLOWED_ALGORITHMS="$allowed" V6_SITE="$SITE" V6_EXTRA_CONFIG="" \
   "$VENV/bin/python" -c 'import os, string, sys; print(string.Template(open(sys.argv[1]).read()).substitute(os.environ), end="")' \
     "$KIT/node.template.yaml" > "$NODE_CFG")
  log "node config $NODE_CFG -> http://$CENTRAL_IP:$V6_SERVER_PORT ($(node_databases | grep -c label) databases)"
}

fw_install() {
  command -v docker >/dev/null || die "Docker is needed: sudo apt-get install -y docker.io && sudo usermod -aG docker \$USER, then log in again"
  docker info >/dev/null 2>&1 || die "this user cannot use Docker: sudo usermod -aG docker \$USER, then log out and in again"
  [ -s "$KIT/credentials/api_key" ] || die "no node API key in $KIT/credentials/api_key"
  chmod 700 "$KIT/credentials" && chmod 600 "$KIT/credentials/api_key"
  make_venv
  uv pip install -q --python "$VENV/bin/python" "vantage6==$VANTAGE6_VERSION"
  docker pull -q "$V6_NODE_IMAGE" >/dev/null
  # the operator's registry is plain HTTP: the Docker daemon must allow it (the node pulls through it)
  local changed
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
    log "added $REGISTRY to Docker's insecure-registries; restarting Docker (needs sudo)"
    sudo systemctl restart docker
  fi
  render_config
  log "vantage6 $VANTAGE6_VERSION node ready (image $V6_NODE_IMAGE)"
}

node_connected() {  # the node's last connection message says connected
  local last
  last="$(docker logs "$CONTAINER" 2>&1 | grep -E 'Websocket connection established|Connected to the /tasks|Disconnected from the server' | tail -1 || true)"
  case "$last" in *Disconnected*|"") return 1 ;; *) return 0 ;; esac
}

fw_start() {
  [ -x "$VENV/bin/v6" ] || die "run ./install.sh first"
  if [ -n "$(docker ps -q --filter "name=^$CONTAINER$")" ]; then
    log "already running (after adding data files: ./stop.sh && ./start.sh)"; return 0
  fi
  tcp_open "$CENTRAL_IP" "$V6_SERVER_PORT" || die "cannot reach the vantage6 server $CENTRAL_IP:$V6_SERVER_PORT (firewall?)"
  render_config   # offers the data files that exist now
  (cd "$HOME" && "$VENV/bin/v6" node start -n "$SITE" --user >/dev/null)
  log "starting node $CONTAINER"
  for _ in $(seq 1 90); do
    if node_connected; then log "connected to the vantage6 server http://$CENTRAL_IP:$V6_SERVER_PORT"; return 0; fi
    sleep 1
  done
  docker logs --tail 15 "$CONTAINER" 2>&1 | grep -v -i api_key >&2
  die "node not connected after 90 s"
}

fw_stop() {
  # `v6 node stop` fails on the root-owned log file (ADR-005); the container is started with auto-remove
  local ids
  ids="$(docker ps -q --filter "name=^$CONTAINER$")"
  if [ -n "$ids" ]; then docker stop "$ids" >/dev/null; log "stopped"; else log "not running"; fi
}

fw_status() {
  if [ -z "$(docker ps -q --filter "name=^$CONTAINER$")" ]; then echo "$SITE: DOWN (container $CONTAINER not running)"; return 1; fi
  if node_connected; then
    echo "$SITE: up, vantage6 node $CONTAINER connected to http://$CENTRAL_IP:$V6_SERVER_PORT"
    docker logs "$CONTAINER" 2>&1 | grep -E 'New task has been added' | tail -1 \
      | sed 's/.*task_id=/  last task_id=/; s/\x1b\[[0-9;]*m//g' | tr -d '\r' || true
  else
    echo "$SITE: DISCONNECTED (container $CONTAINER, server http://$CENTRAL_IP:$V6_SERVER_PORT)"; return 1
  fi
}

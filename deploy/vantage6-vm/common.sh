# Shared helpers for the vantage6 VM scripts (sourced, not run). Needs REPO, topology/versions env.
V6_DATA_DIR="${V6_DATA_DIR:-$HOME/flhub-data}"
V6_NODE_IMAGE="${V6_NODE_IMAGE:-ghcr.io/vantage6/infrastructure/node:$VANTAGE6_VERSION}"

# vantage6 database label of a data variant; must equal adapters.vantage6.service.db_label (ADR-014)
db_label() { echo "$1" | sed 's/[^A-Za-z0-9_]/_/g'; }

# node `databases:` entries of one site: the stage-1 file as `default` (if present) and one entry
# per stage-2 variant file <variant>-<site>.csv
node_databases() {
  local site="$1" f v
  if [ -f "$V6_DATA_DIR/$site.csv" ]; then
    printf -- '- label: default\n  uri: %s\n  type: csv\n' "$V6_DATA_DIR/$site.csv"
  fi
  for f in "$V6_DATA_DIR"/*-"$site".csv; do
    [ -f "$f" ] || continue
    v="$(basename "$f" "-$site.csv")"
    printf -- '- label: %s\n  uri: %s\n  type: csv\n' "$(db_label "$v")" "$f"
  done
}

# render_node_config NODE SITE API_KEY SERVER_URL ALLOWED_IMAGE_REGEX [EXTRA_YAML]
# writes ~/.config/vantage6/node/NODE.yaml (mode 600) from node.template.yaml
render_node_config() {
  local node="$1" site="$2"
  mkdir -p "$HOME/.config/vantage6/node"
  (umask 077; V6_API_KEY="$3" V6_SERVER_URL="$4" V6_SERVER_PORT="$V6_SERVER_PORT" \
    V6_TASK_DIR="$HOME/.local/share/vantage6/node/$node" V6_DATABASES="$(node_databases "$site")" \
    V6_NODE_IMAGE="$V6_NODE_IMAGE" V6_ALLOWED_ALGORITHMS="$5" V6_SITE="$node" V6_EXTRA_CONFIG="${6:-}" \
    envsubst '${V6_API_KEY} ${V6_SERVER_URL} ${V6_SERVER_PORT} ${V6_TASK_DIR} ${V6_DATABASES} ${V6_NODE_IMAGE} ${V6_ALLOWED_ALGORITHMS} ${V6_SITE} ${V6_EXTRA_CONFIG}' \
    < "$REPO/deploy/vantage6-vm/node.template.yaml" > "$HOME/.config/vantage6/node/$node.yaml")
}

# wait_connected NODE: until the node's last connection log line says connected (max 90 s)
wait_connected() {
  local node="$1" last=""
  for _ in $(seq 1 90); do
    last="$(docker logs "vantage6-$node-user" 2>&1 | grep -E 'Websocket connection established|Connected to the /tasks|Disconnected from the server' | tail -1 || true)"
    case "$last" in *Disconnected*|"") sleep 1 ;; *) return 0 ;; esac
  done
  echo "node $node not connected; docker logs vantage6-$node-user:" >&2
  docker logs --tail 15 "vantage6-$node-user" 2>&1 | grep -v -i api_key >&2
  return 1
}

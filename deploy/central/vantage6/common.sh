# Shared helpers for deploy/central/vantage6 (sourced). Needs REPO, RT, NAME, topology/versions env
# and the generated secrets (V6_JWT_SECRET) in the environment.
V6_SERVER_IMAGE="${V6_SERVER_IMAGE:-ghcr.io/vantage6/infrastructure/server:$VANTAGE6_VERSION}"
V6_SERVER_BIND="${V6_SERVER_BIND:-0.0.0.0}"   # host address the server port is published on
CFG="$HOME/.config/vantage6/server/$NAME.yaml"

# server config from server.template.yaml (written by hand: `v6 server new` is interactive only)
render_server_config() {
  mkdir -p "$(dirname "$CFG")"
  (umask 077; V6_SERVER_PORT="$V6_SERVER_PORT" V6_SERVER_IMAGE="$V6_SERVER_IMAGE" V6_SERVER_BIND="$V6_SERVER_BIND" \
    envsubst '${V6_SERVER_PORT} ${V6_JWT_SECRET} ${V6_SERVER_IMAGE} ${V6_SERVER_BIND}' \
    < "$REPO/deploy/central/vantage6/server.template.yaml" > "$CFG")
}

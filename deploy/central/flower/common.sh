# Shared settings for the Flower server side on the central VM: SuperLink + adapter (+ relay run).
# Sourced by install.sh / start.sh / stop.sh / status.sh. Overridable via environment:
#   FLOWER_FLEET_BIND    Fleet API bind address (default 0.0.0.0:9092; island: 127.0.0.1:9092)
#   FLHUB_HUB_URL        hub core (default http://127.0.0.1:8800)
#   FLHUB_FLOWER_VENV    venv (default ~/venvs/flower)
#   FLHUB_FLOWER_RUNTIME runtime dir for pids, logs, FLWR_HOME (default ~/flhub-runtime/flower)
#   FLHUB_FLOWER_CONTROL_PORT / FLHUB_ADAPTER_FLOWER_PORT  ports (default: topology.env; a second
#                        SuperLink next to the live one, e.g. a test, uses its own)
#   FLHUB_FLOWER_SITES   adapter sites (default: the testbed's flw-* sites)
#   FLOWER_TLS           1: TLS on all SuperLink APIs and SuperNode authentication (certificates
#                        from tls.sh, nodes registered with register_node.sh); 0: --insecure, no auth.
#                        Default: 1 once tls.sh has made $TLS_DIR/server.crt, else 0. FLOWER_TLS=0 with
#                        a certificate present is refused unless FLOWER_ROLLBACK=1 (rollback only).
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
. "$REPO/deploy/versions.env"
. "$REPO/deploy/topology.env"
VENV="${FLHUB_FLOWER_VENV:-$HOME/venvs/flower}"
RUNTIME="${FLHUB_FLOWER_RUNTIME:-$HOME/flhub-runtime/flower}"
FLEET_BIND="${FLOWER_FLEET_BIND:-0.0.0.0:$FLOWER_FLEET_PORT}"
CONTROL_PORT="${FLHUB_FLOWER_CONTROL_PORT:-$FLOWER_CONTROL_PORT}"   # SuperLink HTTP API (control + ServerApp runtime), local only
ADAPTER_PORT="${FLHUB_ADAPTER_FLOWER_PORT:-$ADAPTER_FLOWER_PORT}"
HUB_URL="${FLHUB_HUB_URL:-http://127.0.0.1:$HUB_CORE_PORT}"
SITES_FLOWER="${FLHUB_FLOWER_SITES:-flw-h5 flw-h6 flw-h11 flw-h12}"   # adapter sites; flw-h11/flw-h12 run only during E5
BASE_SITES_FLOWER="${FLHUB_FLOWER_BASE_SITES:-flw-h5 flw-h6}"             # status.sh health covers these
FLWR_HOME_DIR="$RUNTIME/flwr-home"    # SuperLink state + flwr CLI connection config
TLS_DIR="$RUNTIME/tls"                # Flower CA + SuperLink certificate (tls.sh), mode 700, never in git
if [ -z "${FLOWER_TLS:-}" ]; then
  if [ -f "$TLS_DIR/server.crt" ]; then FLOWER_TLS=1; else FLOWER_TLS=0; fi
fi
NODES_MAP="$RUNTIME/nodes.map"        # registered SuperNodes: site node_id pubkey_sha256 (register_node.sh)
export PATH="$VENV/bin:$PATH"         # flower-superexec / flwr-serverapp are found via PATH

pid_alive() { [ -f "$1" ] && kill -0 "$(cat "$1")" 2>/dev/null; }
port_open() { (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }

write_flwr_config() {  # the flwr CLI connection "flhub" to this SuperLink (adapter: flwr run/stop; register_node.sh)
  mkdir -p "$FLWR_HOME_DIR"
  {
    echo "# flwr CLI connection to the local SuperLink (written by deploy/central/flower, FLOWER_TLS=$FLOWER_TLS)"
    echo "[superlink]"; echo 'default = "flhub"'; echo
    echo "[superlink.flhub]"; echo "address = \"127.0.0.1:$CONTROL_PORT\""
    if [ "$FLOWER_TLS" = 1 ]; then echo "root-certificates = \"$TLS_DIR/ca.crt\""; else echo "insecure = true"; fi
  } > "$FLWR_HOME_DIR/config.toml"
}

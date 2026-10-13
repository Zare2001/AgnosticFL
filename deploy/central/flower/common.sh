# Shared settings for the Flower server side on the central VM: SuperLink + adapter (+ relay run).
# Sourced by install.sh / start.sh / stop.sh / status.sh. Overridable via environment:
#   FLOWER_FLEET_BIND    Fleet API bind address (default 0.0.0.0:9092; island: 127.0.0.1:9092)
#   FLHUB_HUB_URL        hub core (default http://127.0.0.1:8800)
#   FLHUB_FLOWER_VENV    venv (default ~/venvs/flower)
#   FLHUB_FLOWER_RUNTIME runtime dir for pids, logs, FLWR_HOME (default ~/flhub-runtime/flower)
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
. "$REPO/deploy/versions.env"
. "$REPO/deploy/topology.env"
VENV="${FLHUB_FLOWER_VENV:-$HOME/venvs/flower}"
RUNTIME="${FLHUB_FLOWER_RUNTIME:-$HOME/flhub-runtime/flower}"
FLEET_BIND="${FLOWER_FLEET_BIND:-0.0.0.0:$FLOWER_FLEET_PORT}"
CONTROL_PORT="$FLOWER_CONTROL_PORT"   # SuperLink HTTP API (control + ServerApp runtime), local only
ADAPTER_PORT="$ADAPTER_FLOWER_PORT"
HUB_URL="${FLHUB_HUB_URL:-http://127.0.0.1:$HUB_CORE_PORT}"
SITES_FLOWER="flw-h5 flw-h6"
FLWR_HOME_DIR="$RUNTIME/flwr-home"    # SuperLink state + flwr CLI connection config
export PATH="$VENV/bin:$PATH"         # flower-superexec / flwr-serverapp are found via PATH

pid_alive() { [ -f "$1" ] && kill -0 "$(cat "$1")" 2>/dev/null; }
port_open() { (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }

# Shared settings for the Flower hospital SuperNodes (flw-h5, flw-h6) on the flower VM.
# Sourced by install.sh / start.sh / stop.sh / status.sh. Overridable via environment:
#   FLHUB_SUPERLINK      SuperLink Fleet API (default: central VM 145.38.205.58:9092;
#                        island mode: 127.0.0.1:9092)
#   FLHUB_FLOWER_VENV    venv (default ~/venvs/flower)
#   FLHUB_FLOWER_RUNTIME runtime dir for pids, logs, FLWR_HOME (default ~/flhub-runtime/flower)
#   FLHUB_DATA_DIR       site CSVs (default ~/flhub-data)
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
. "$REPO/deploy/versions.env"
. "$REPO/deploy/topology.env"
VENV="${FLHUB_FLOWER_VENV:-$HOME/venvs/flower}"
RUNTIME="${FLHUB_FLOWER_RUNTIME:-$HOME/flhub-runtime/flower}"
DATA_DIR="${FLHUB_DATA_DIR:-$HOME/flhub-data}"
SUPERLINK="${FLHUB_SUPERLINK:-$FLHUB_CENTRAL_IP:$FLOWER_FLEET_PORT}"
NODES="flw-h5:9094 flw-h6:9095"   # site_id:local SuperNode runtime API port
export PATH="$VENV/bin:$PATH"     # flower-superexec / flwr-clientapp are found via PATH

pid_alive() { [ -f "$1" ] && kill -0 "$(cat "$1")" 2>/dev/null; }

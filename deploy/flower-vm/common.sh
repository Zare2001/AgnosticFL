# Shared settings for the Flower hospital SuperNodes (flw-h5, flw-h6) on the flower VM.
# Sourced by install.sh / start.sh / stop.sh / status.sh. Overridable via environment:
#   FLHUB_SUPERLINK      SuperLink Fleet API (default: central VM 145.38.205.58:9092;
#                        island mode: 127.0.0.1:9092)
#   FLHUB_FLOWER_VENV    venv (default ~/venvs/flower)
#   FLHUB_FLOWER_RUNTIME runtime dir for pids, logs, FLWR_HOME (default ~/flhub-runtime/flower)
#   FLHUB_DATA_DIR       site CSVs (default ~/flhub-data)
#   FLHUB_FLOWER_NODES   "site:port ..." (default flw-h5:9094 flw-h6:9095; island tests use other ports)
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
. "$REPO/deploy/versions.env"
. "$REPO/deploy/topology.env"
VENV="${FLHUB_FLOWER_VENV:-$HOME/venvs/flower}"
RUNTIME="${FLHUB_FLOWER_RUNTIME:-$HOME/flhub-runtime/flower}"
DATA_DIR="${FLHUB_DATA_DIR:-$HOME/flhub-data}"
SUPERLINK="${FLHUB_SUPERLINK:-$FLHUB_CENTRAL_IP:$FLOWER_FLEET_PORT}"
NODES="${FLHUB_FLOWER_NODES:-flw-h5:9094 flw-h6:9095}"   # site_id:local SuperNode runtime API port
export PATH="$VENV/bin:$PATH"     # flower-superexec / flwr-clientapp are found via PATH

pid_alive() { [ -f "$1" ] && kill -0 "$(cat "$1")" 2>/dev/null; }
descendants() { local c; for c in $(pgrep -P "$1"); do echo "$c"; descendants "$c"; done; }
node_port() { local e; for e in $NODES; do [ "${e%%:*}" = "$1" ] && echo "${e##*:}"; done; return 0; }
supernode_pid() {  # the site's flower-supernode process (child of its supervisor supernode.sh)
  local pidf="$RUNTIME/run/supernode-$1.pid"
  pid_alive "$pidf" && pgrep -P "$(cat "$pidf")" -f flower-supernode | head -1
}
stop_site() {  # stop_site <site>: stop one SuperNode with its supervisor and children (idempotent)
  local site="$1" pidf="$RUNTIME/run/supernode-$1.pid" pid kids
  if ! pid_alive "$pidf"; then echo "$site: not running"; rm -f "$pidf"; return 0; fi
  pid="$(cat "$pidf")"
  kids="$(descendants "$pid")"
  kill "$pid"
  for _ in $(seq 20); do kill -0 "$pid" 2>/dev/null || break; sleep 0.5; done
  kill -0 "$pid" 2>/dev/null && kill -9 "$pid"
  for k in $kids; do kill "$k" 2>/dev/null || true; done
  rm -f "$pidf"
  echo "$site: stopped (pid $pid)"
}

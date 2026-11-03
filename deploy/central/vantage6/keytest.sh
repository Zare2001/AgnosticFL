#!/usr/bin/env bash
# Side-by-side test of hospital-made node keys (central), next to the live federation.
# Collaboration flhub-keytest: hub-operator, v6-h3, v6-h4; each hospital has a second node there
# (v6-h3k / v6-h4k on the vantage6 VM, deploy/vantage6-vm/keytest.sh) with a key it made itself.
# A second adapter on 127.0.0.1:8822 serves only that collaboration; the live adapter (:8812,
# collaboration flhub) and its nodes are not touched.
#   register SITE KEYFILE FP   register_node.sh SITE KEYFILE --collab flhub-keytest --fingerprint FP
#   adapter start|stop|status  the keytest adapter on :8822 (log ~/flhub-runtime/vantage6/keytest-adapter.log)
#   conformance                the conformance suite against :8822 (sites v6-h3 v6-h4)
#   clean                      stop the keytest adapter; delete the nodes of flhub-keytest and the
#                              collaboration (organisations v6-h3/v6-h4 and their flhub nodes stay)
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../../.." && pwd)"
. "$REPO/deploy/topology.env"
RT="${V6_RT:-$HOME/flhub-runtime/vantage6}"
VENV="${V6_VENV:-$HOME/venvs/v6}"
COLLAB=flhub-keytest
PORT=8822
SITES_T="v6-h3 v6-h4"
PID="$RT/keytest-adapter.pid"
LOGF="$RT/keytest-adapter.log"
log() { echo "[v6-keytest] $*"; }
SERVER_URL="$(grep '^V6_SERVER_URL=' "$RT/adapter.env" | cut -d= -f2-)"

case "${1:-}" in
  register)
    bash "$REPO/deploy/central/vantage6/register_node.sh" "${2:?site}" "${3:?keyfile}" --collab "$COLLAB" --fingerprint "${4:?fingerprint}" ;;
  adapter)
    case "${2:-}" in
      start)
        if [ -f "$PID" ] && kill -0 "$(cat "$PID")" 2>/dev/null; then log "keytest adapter already running (pid $(cat "$PID"))"; exit 0; fi
        pw="$(grep '^V6_ADAPTER_PASSWORD=' "$RT/secrets.env" | cut -d= -f2-)"
        (set -a; . "$RT/adapter.env"; set +a; cd "$REPO"
         V6_ADAPTER_PASSWORD="$pw" nohup "$VENV/bin/python" -m adapters.vantage6 --port "$PORT" \
           --collaboration "$COLLAB" --sites $SITES_T >>"$LOGF" 2>&1 </dev/null &
         echo $! > "$PID")
        for _ in $(seq 1 30); do curl -sf "http://127.0.0.1:$PORT/health" >/dev/null && break; sleep 1; done
        curl -sf "http://127.0.0.1:$PORT/health" >/dev/null || { tail -20 "$LOGF" >&2; exit 1; }
        log "keytest adapter up on 127.0.0.1:$PORT (pid $(cat "$PID"), collaboration $COLLAB, log $LOGF)" ;;
      stop)
        if [ -f "$PID" ]; then kill "$(cat "$PID")" 2>/dev/null || true; rm -f "$PID"; log "keytest adapter stopped"; fi ;;
      status)
        curl -sf "http://127.0.0.1:$PORT/health" && echo || { echo "keytest adapter DOWN"; exit 1; } ;;
      *) echo "usage: $0 adapter start|stop|status" >&2; exit 2 ;;
    esac ;;
  conformance)
    cd "$REPO"
    "$VENV/bin/python" -m pytest tests/conformance --adapter "http://127.0.0.1:$PORT" --hub http://127.0.0.1:8800 \
      --sites $SITES_T --restart-cmd "bash $REPO/deploy/central/vantage6/keytest.sh adapter stop && bash $REPO/deploy/central/vantage6/keytest.sh adapter start" \
      --variant organamnist-dir0.3 --report "$RT/conformance-v6-keytest.json" -v -s ;;
  clean)
    bash "$0" adapter stop
    pw="$(grep '^V6_ROOT_PASSWORD=' "$RT/secrets.env" | cut -d= -f2-)"
    V6_ROOT_PASSWORD="$pw" "$VENV/bin/python" - "$SERVER_URL" "$V6_SERVER_PORT" "$COLLAB" <<'PY'
import logging, os, sys
from vantage6.client import UserClient

url, port, collab_name = sys.argv[1], int(sys.argv[2]), sys.argv[3]
root = logging.getLogger(); handlers = list(root.handlers)
client = UserClient(url, port, "/api", log_level="warn")
root.handlers[:] = handlers
client.authenticate("root", os.environ["V6_ROOT_PASSWORD"])
client.setup_encryption(None)
collab = next((c for c in client.collaboration.list(per_page=100)["data"] if c["name"] == collab_name), None)
if collab is None:
    print(f"[v6-keytest] no collaboration {collab_name}: nothing to clean"); sys.exit(0)
for n in client.node.list(collaboration=collab["id"], per_page=100)["data"]:
    client.node.delete(n["id"]); print(f"[v6-keytest] deleted node {n['id']} {n['name']}")
client.collaboration.delete(collab["id"], delete_dependents=True)
print(f"[v6-keytest] deleted collaboration {collab_name} (id {collab['id']})")
PY
    ;;
  *) echo "usage: $0 register SITE KEYFILE FP | adapter start|stop|status | conformance | clean" >&2; exit 2 ;;
esac

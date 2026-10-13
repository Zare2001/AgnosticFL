#!/usr/bin/env bash
# Between runs (E4a disk safety): delete every vantage6 task with its runs and results (input and
# results live in the server's sqlite DB) through the API as root. Refuses while a task is still
# open (pending/initializing/active). --vacuum also stops the server, VACUUMs the sqlite file
# (sqlite keeps freed pages otherwise) and starts it again (~20 s; nodes and adapter reconnect).
# Usage: bash deploy/central/vantage6/purge.sh [--vacuum]
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../../.." && pwd)"
. "$REPO/deploy/topology.env"
. "$REPO/deploy/versions.env"
NAME=flhub
RT="${V6_RT:-$HOME/flhub-runtime/vantage6}"
VENV="${V6_VENV:-$HOME/venvs/v6}"
DB="$HOME/.local/share/vantage6/server/$NAME/$NAME.sqlite"
. "$REPO/deploy/central/vantage6/common.sh"
SERVER_URL="$(grep '^V6_SERVER_URL=' "$RT/adapter.env" | cut -d= -f2-)"
log() { echo "[v6-purge] $(date -u +%H:%M:%SZ) $*"; }
size() { du -h "$DB" 2>/dev/null | cut -f1; }

log "server DB $(size) before"
pw="$(grep '^V6_ROOT_PASSWORD=' "$RT/secrets.env" | cut -d= -f2-)"
V6_ROOT_PASSWORD="$pw" "$VENV/bin/python" - "$SERVER_URL" "$V6_SERVER_PORT" <<'PY'
import logging, os, sys
from vantage6.client import UserClient

url, port = sys.argv[1], int(sys.argv[2])
root = logging.getLogger(); handlers = list(root.handlers)
client = UserClient(url, port, "/api", log_level="warn")
root.handlers[:] = handlers
client.log = logging.getLogger("v6-purge"); client.log.setLevel(logging.ERROR)
client.authenticate("root", os.environ["V6_ROOT_PASSWORD"])
client.setup_encryption(None)
tasks, page = [], 1
while True:
    res = client.request("task", params={"page": page, "per_page": 100})
    tasks += res["data"]
    if len(res["data"]) < 100:
        break
    page += 1
open_ = [t["id"] for t in tasks if t.get("status") in ("pending", "initializing", "active")]
if open_:
    sys.exit(f"[v6-purge] refusing: vantage6 tasks still open: {open_}")
for t in tasks:
    client.request(f"task/{t['id']}", method="delete")
print(f"[v6-purge] deleted {len(tasks)} vantage6 tasks with their runs and results")
PY
if [ "${1:-}" = "--vacuum" ]; then
  ids="$(docker ps -q --filter "label=vantage6-type=server" --filter "label=name=$NAME")"
  [ -z "$ids" ] || docker stop $ids >/dev/null
  for _ in $(seq 1 30); do
    [ -z "$(docker ps -aq --filter "label=vantage6-type=server" --filter "label=name=$NAME")" ] && break; sleep 0.5
  done
  docker run --rm --network none -v "$(dirname "$DB"):/d" --entrypoint python "$V6_SERVER_IMAGE" \
    -c "import sqlite3; c = sqlite3.connect('/d/$NAME.sqlite'); c.execute('VACUUM'); c.close()"
  bash "$REPO/deploy/central/vantage6/start.sh" | grep 'server up'
  log "VACUUM done, server restarted"
fi
log "server DB $(size) after; disk: $(df -h / | awk 'NR == 2 {print $4 " free"}')"

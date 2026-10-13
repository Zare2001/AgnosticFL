#!/usr/bin/env bash
# Reclaim the Flower SuperLink's disk between E4a payload runs (PLAN.md §7). With --database the
# SuperLink keeps message objects (the weights, in 5 MiB chunks) in SQLite; they are deleted once
# delivered, but SQLite keeps the freed pages and the WAL file, so the DB stays at its peak size
# (about 3 x payload x sites per round). This script stops the adapter and the SuperLink, checkpoints
# the WAL and VACUUMs the DB (node rows stay: SuperNodes reconnect with the same ids), then starts
# both again and waits until the relay is back and the base sites are online.
#   bash deploy/central/flower/purge.sh            # offline purge (refuses while a round is in flight)
#   bash deploy/central/flower/purge.sh --online   # WAL checkpoint only, nothing restarted
set -euo pipefail
. "$(dirname "$0")/common.sh"
DB="$RUNTIME/superlink.db"

sizes() {
  local f total=0
  for f in "$DB" "$DB-wal" "$DB-shm"; do [ -f "$f" ] && total=$((total + $(stat -c %s "$f"))); done
  echo "superlink db+wal+shm $((total / 1048576)) MiB ($(ls -l "$DB"* 2>/dev/null | awk '{printf "%s=%d ", $NF, $5}'))"
}
sqlite() {  # sqlite <sql...>: run statements on the DB with the venv's python (stdlib sqlite3)
  "$VENV/bin/python" - "$DB" "$@" <<'PY'
import sqlite3, sys
con = sqlite3.connect(sys.argv[1], timeout=30, isolation_level=None)
for sql in sys.argv[2:]:
    print(sql, "->", con.execute(sql).fetchall())
con.close()
PY
}

echo "before: $(sizes); free disk $(df -h --output=avail / | tail -1 | tr -d ' ')"
if [ "${1:-}" = "--online" ]; then
  sqlite "PRAGMA wal_checkpoint(TRUNCATE)"
  echo "after:  $(sizes)"
  exit 0
fi

pending="$(curl -sf "http://127.0.0.1:$ADAPTER_PORT/relay/status" | "$VENV/bin/python" -c \
  'import json,sys; print(len(json.load(sys.stdin)["pending"]))' 2>/dev/null || echo 0)"
[ "$pending" = 0 ] || { echo "a round is in flight ($pending pending): not purging" >&2; exit 1; }

bash "$(dirname "$0")/stop.sh"
sqlite "PRAGMA wal_checkpoint(TRUNCATE)" "PRAGMA freelist_count" "VACUUM" "PRAGMA freelist_count"
echo "after:  $(sizes); free disk $(df -h --output=avail / | tail -1 | tr -d ' ')"
bash "$(dirname "$0")/start.sh"
for _ in $(seq 90); do
  bash "$(dirname "$0")/status.sh" >/dev/null 2>&1 && { bash "$(dirname "$0")/status.sh" | tail -3; exit 0; }
  sleep 2
done
echo "flower server side not HEALTHY 180 s after the purge" >&2
bash "$(dirname "$0")/status.sh" || true
exit 1

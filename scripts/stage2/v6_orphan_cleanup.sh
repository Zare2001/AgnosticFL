#!/usr/bin/env bash
# Remove vantage6 run rows orphaned by task deletion, then VACUUM (central, between runs).
# vantage6 4.15.2 deletes a task's subtasks without their runs (server/resource/task.py, vantage6 #804),
# so every E7 native central-function task leaves its subtasks' runs, with input and result, behind
# with task_id NULL; they are unreachable through the API. As purge.sh --vacuum does: stop the server,
# run python/sqlite3 in the server image on the DB file, start the server again (nodes reconnect).
#   bash scripts/stage2/v6_orphan_cleanup.sh
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
. "$REPO/deploy/topology.env"
. "$REPO/deploy/versions.env"
NAME=flhub
DB="$HOME/.local/share/vantage6/server/$NAME/$NAME.sqlite"
log() { echo "[v6-orphans] $(date -u +%H:%M:%SZ) $*"; }
[ ! -e "$HOME/flhub-runtime/runs/stage2/ACTIVE_RUN" ] || { log "refusing: a run is active"; exit 1; }
log "server DB $(du -h "$DB" | cut -f1) before"
ids="$(docker ps -q --filter "label=vantage6-type=server" --filter "label=name=$NAME")"
[ -z "$ids" ] || docker stop $ids >/dev/null
for _ in $(seq 1 30); do
  [ -z "$(docker ps -aq --filter "label=vantage6-type=server" --filter "label=name=$NAME")" ] && break; sleep 0.5
done
docker run --rm --network none -v "$(dirname "$DB"):/d" --entrypoint python "$V6_SERVER_IMAGE" -c "
import sqlite3
c = sqlite3.connect('/d/$NAME.sqlite')
tables = {r[0] for r in c.execute(\"select name from sqlite_master where type='table'\")}
n_run = c.execute('select count(*) from run where task_id is null').fetchone()[0]
if 'algorithmport' in tables:
    c.execute('delete from algorithmport where run_id in (select id from run where task_id is null)')
c.execute('delete from run where task_id is null')
n_td = c.execute('select count(*) from taskdatabase where task_id is null or task_id not in (select id from task)').fetchone()[0]
c.execute('delete from taskdatabase where task_id is null or task_id not in (select id from task)')
c.commit()
c.execute('VACUUM')
c.close()
print(f'deleted {n_run} orphaned runs and {n_td} orphaned taskdatabase rows; algorithmport table: {\"algorithmport\" in tables}')
"
bash "$REPO/deploy/central/vantage6/start.sh" | grep 'server up'
log "server DB $(du -h "$DB" | cut -f1) after; disk: $(df -h / | awk 'NR == 2 {print $4 " free"}')"

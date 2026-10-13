#!/usr/bin/env bash
# Keep one hospital SuperNode running (started by start.sh; stop with stop.sh).
# A SuperNode exits by design when the SuperLink no longer knows its node id (for example a
# SuperLink restarted with fresh state answers UNAUTHENTICATED); this loop starts it again.
#   usage: supernode.sh <site_id> <local runtime port>
set -u
. "$(dirname "$0")/common.sh"
site="$1"; port="$2"; child=""; stopping=0
mkdir -p "$RUNTIME/$site" && cd "$RUNTIME/$site"   # neutral cwd: nothing from the repo on sys.path
trap 'stopping=1; [ -n "$child" ] && kill "$child" 2>/dev/null' TERM INT

while [ "$stopping" = 0 ]; do
  FLWR_HOME="$RUNTIME/$site" flower-supernode --insecure --superlink "$SUPERLINK" --port "$port" \
      --node-config "site-id='$site' data-path='$DATA_DIR/$site.csv'" &
  child=$!
  wait "$child"; rc=$?
  if [ "$stopping" = 1 ]; then wait "$child" 2>/dev/null; break; fi
  echo "$(date -u +%FT%TZ) flhub: supernode $site exited rc=$rc, restarting in 10 s"
  sleep 10 & wait $!
done

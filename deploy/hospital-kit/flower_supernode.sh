#!/usr/bin/env bash
# Keeps this hospital's flower-supernode running (started by start.sh, ended by stop.sh). A SuperNode
# exits by design when the SuperLink no longer knows its node id (e.g. after a SuperLink restart with
# fresh state); this loop starts it again.
. "$(dirname "$0")/lib.sh"
set +e
echo $$ > "$RUN/supernode.pid"   # this loop is what stop.sh ends (with its children)
export PATH="$VENV/bin:$PATH"   # flwr-clientapp is found via PATH
mkdir -p "$KIT/flwr-home" && cd "$KIT/flwr-home" || exit 1   # neutral cwd: nothing else on sys.path
child=""; stopping=0
trap 'stopping=1; [ -n "$child" ] && kill "$child" 2>/dev/null' TERM INT
while [ "$stopping" = 0 ]; do
  FLWR_HOME="$KIT/flwr-home" flower-supernode --insecure --superlink "$CENTRAL_IP:$FLOWER_FLEET_PORT" \
    --port "$FLOWER_LOCAL_PORT" --node-config "site-id='$SITE' data-path='$DATA_DIR/$SITE.csv'" &
  child=$!
  wait "$child"; rc=$?
  if [ "$stopping" = 1 ]; then wait "$child" 2>/dev/null; break; fi
  echo "$(date -u +%FT%TZ) flhub: supernode $SITE exited rc=$rc, restarting in 10 s"
  sleep 10 & wait $!
done

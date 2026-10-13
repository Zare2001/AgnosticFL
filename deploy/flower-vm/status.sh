#!/usr/bin/env bash
# Health of the hospital SuperNodes: process alive and a TCP connection to the SuperLink.
# Exit 0 only if both SuperNodes are healthy. (Authoritative view: the adapter's discover.)
set -uo pipefail
. "$(dirname "$0")/common.sh"

ok=0
host="${SUPERLINK%:*}"; port="${SUPERLINK##*:}"
for entry in $NODES; do
  site="${entry%%:*}"
  pidf="$RUNTIME/run/supernode-$site.pid"
  if ! pid_alive "$pidf"; then
    echo "$site: DOWN (not running)"; ok=1; continue
  fi
  pid="$(pgrep -P "$(cat "$pidf")" | head -1)"   # flower-supernode under supernode.sh
  if [ -z "$pid" ]; then
    echo "$site: DOWN (supernode.sh running, no flower-supernode child; restarting?)"; ok=1; continue
  fi
  if ss -tnp state established 2>/dev/null | grep "pid=$pid," | grep -q "$host\]*:$port"; then
    conn="connected to $SUPERLINK"
  else
    conn="NOT connected to $SUPERLINK"; ok=1
  fi
  node_id="$(grep -ao 'SuperNode ID: [0-9]*' "$RUNTIME/logs/supernode-$site.log" | tail -1)"
  echo "$site: running pid $pid, $conn, ${node_id:-no node id yet}"
done
if crontab -l 2>/dev/null | grep -q 'deploy/flower-vm/start.sh' && [ -f "$RUNTIME/nodes.enabled" ]; then
  echo "watchdog: enabled (cron, superlink $(cat "$RUNTIME/nodes.enabled"))"
else
  echo "watchdog: not active"
fi
[ "$ok" = 0 ] && echo "flower-vm: HEALTHY" || echo "flower-vm: UNHEALTHY"
exit "$ok"

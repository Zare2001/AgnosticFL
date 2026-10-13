#!/usr/bin/env bash
# Health of the NVFlare hospital clients on this VM: status.sh [site ...]
# A site is healthy when its client process runs and holds a TLS connection to the server.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/common.sh"
sites=("$@"); [ ${#sites[@]} -eq 0 ] && read -ra sites <<<"$NVF_SITES"
ok=1

for site in "${sites[@]}"; do
  ws="$NVF_SITES_DIR/$site"
  if [ ! -f "$ws/startup/fed_client.json" ]; then
    echo "$site: NO KIT ($ws)"; ok=0; continue
  fi
  target=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["servers"][0]["service"]["target"])' \
    "$ws/startup/fed_client.json")
  host=${target%:*}; port=${target##*:}
  ip=$(getent ahostsv4 "$host" | awk '{print $1; exit}')
  if ! nvf_pid_alive "$ws/pid.fl"; then
    echo "$site: DOWN (no client process; server $target)"; ok=0; continue
  fi
  pid=$(cat "$ws/pid.fl")
  conns=$(ss -Htnp state established "( dport = :$port )" 2>/dev/null | grep -c "pid=$pid," || true)
  jobs=$(pgrep -f "worker_process.*$ws" | wc -l)
  if [ "$conns" -ge 1 ]; then
    echo "$site: up (pid $pid, connected to $target = ${ip:-?}, job processes: $jobs, data $FLHUB_DATA_DIR/$site.csv)"
  else
    echo "$site: NOT CONNECTED (pid $pid, server $target = ${ip:-?})"; ok=0
  fi
done
[ "$ok" = 1 ] && echo "status: HEALTHY" || echo "status: NOT HEALTHY"
[ "$ok" = 1 ]

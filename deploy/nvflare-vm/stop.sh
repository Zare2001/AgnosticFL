#!/usr/bin/env bash
# Stops NVFlare hospital clients: stop.sh [site ...]. Uses the kit's own shutdown.fl mechanism
# (what startup/stop_fl.sh does, without its interactive prompt). Idempotent.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/common.sh"
sites=("$@"); [ ${#sites[@]} -eq 0 ] && read -ra sites <<<"$NVF_SITES"

for site in "${sites[@]}"; do
  ws="$NVF_SITES_DIR/$site"
  if nvf_pid_alive "$ws/daemon_pid.fl"; then
    dpid=$(cat "$ws/daemon_pid.fl")
    touch "$ws/shutdown.fl"
    for _ in $(seq 1 40); do kill -0 "$dpid" 2>/dev/null || break; sleep 1; done
    kill -0 "$dpid" 2>/dev/null && { echo "stop: $site daemon still alive" >&2; exit 1; }
    echo "stop: $site stopped"
  else
    echo "stop: $site not running"
  fi
  # job processes (worker_process) that outlived the client process
  pkill -f "nvflare.private.fed.app.client.worker_process.*$ws" 2>/dev/null || true
  rm -f "$ws/shutdown.fl"
done
echo "stop: OK"

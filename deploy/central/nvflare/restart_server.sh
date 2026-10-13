#!/usr/bin/env bash
# E6e: restarts ONLY the NVFlare server on central (not the adapter) with the kit's own mechanism
# (shutdown.fl, then the kit's start.sh), and returns once the server ports accept connections again.
# No manual step afterwards: the hospitals reconnect by themselves and the adapter's keeper submits a
# new relay job once they are back (ADR-011). Prints the timeline.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/common.sh"
export PATH="$NVF_VENV/bin:$PATH"
t0=$(date +%s.%N); ts() { python3 -c "print(f'{$(date +%s.%N) - $t0:6.1f}s')"; }
echo "restart_server: $(date -u +%FT%TZ) restarting the NVFlare server ($NVF_SERVER_NAME)"
if nvf_pid_alive "$NVF_SERVER_KIT/daemon_pid.fl"; then
  dpid=$(cat "$NVF_SERVER_KIT/daemon_pid.fl")
  touch "$NVF_SERVER_KIT/shutdown.fl"
  for _ in $(seq 1 60); do kill -0 "$dpid" 2>/dev/null || break; sleep 1; done
  kill -0 "$dpid" 2>/dev/null && { echo "restart_server: server daemon still alive after 60 s" >&2; exit 1; }
fi
pkill -f "nvflare.private.fed.app.server.runner_process.*$NVF_SERVER_KIT" 2>/dev/null || true  # job processes
echo "restart_server: $(ts) server stopped"
rm -f "$NVF_SERVER_KIT/shutdown.fl"
(exec > "$NVF_LOGS/server-start.out" 2>&1 < /dev/null; cd "$NVF_SERVER_KIT" && setsid nohup bash startup/start.sh &)
for _ in $(seq 1 90); do
  nvf_port_open "$NVF_FL_PORT" && nvf_port_open "$NVF_ADMIN_PORT" && break
  sleep 1
done
nvf_port_open "$NVF_ADMIN_PORT" || { echo "restart_server: ports not open after 90 s" >&2; tail -20 "$NVF_SERVER_KIT/log.txt" >&2; exit 1; }
echo "restart_server: $(date -u +%FT%TZ) server up again after $(ts) (relay job follows via the adapter keeper)"

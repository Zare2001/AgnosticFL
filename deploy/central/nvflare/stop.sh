#!/usr/bin/env bash
# Stops the NVFlare adapter (it ends the relay job) and then the NVFlare server. Idempotent.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/common.sh"

wait_gone() {  # pid, seconds
  for _ in $(seq 1 "$2"); do kill -0 "$1" 2>/dev/null || return 0; sleep 1; done
  return 1
}

if nvf_pid_alive "$NVF_ADAPTER_PID"; then
  pid=$(cat "$NVF_ADAPTER_PID")
  kill -TERM "$pid"  # the adapter asks the relay to end the job, then exits
  wait_gone "$pid" 40 || { echo "stop: adapter did not exit, killing"; kill -9 "$pid" 2>/dev/null || true; }
  echo "stop: adapter stopped"
else
  echo "stop: adapter not running"
fi
rm -f "$NVF_ADAPTER_PID"

if nvf_pid_alive "$NVF_SERVER_KIT/daemon_pid.fl"; then
  dpid=$(cat "$NVF_SERVER_KIT/daemon_pid.fl")
  touch "$NVF_SERVER_KIT/shutdown.fl"  # standard kit mechanism: the start.sh daemon stops the server
  wait_gone "$dpid" 60 || { echo "stop: server daemon still alive" >&2; exit 1; }
  echo "stop: NVFlare server stopped"
else
  echo "stop: NVFlare server not running"
fi
# server job processes that outlived their parent
pkill -f "nvflare.private.fed.app.server.runner_process.*$NVF_SERVER_KIT" 2>/dev/null || true
echo "stop: OK"

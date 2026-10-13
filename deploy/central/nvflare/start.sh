#!/usr/bin/env bash
# Starts the NVFlare server (standard startup kit) and the NVFlare adapter on 127.0.0.1:8811.
# The adapter submits the long-running relay job as soon as the server accepts admin logins.
# Idempotent: running parts are left alone.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/common.sh"
export PATH="$NVF_VENV/bin:$PATH"
[ -d "$NVF_SERVER_KIT/startup" ] || { echo "start: no server kit in $NVF_SERVER_KIT; run install.sh" >&2; exit 1; }
mkdir -p "$NVF_LOGS"

if nvf_pid_alive "$NVF_SERVER_KIT/daemon_pid.fl"; then
  echo "start: NVFlare server already running"
else
  rm -f "$NVF_SERVER_KIT/shutdown.fl"
  (cd "$NVF_SERVER_KIT" && setsid nohup bash startup/start.sh > "$NVF_LOGS/server-start.out" 2>&1 < /dev/null &)
  echo "start: NVFlare server starting ($NVF_SERVER_NAME, fed $NVF_FL_PORT, admin $NVF_ADMIN_PORT)"
fi
for _ in $(seq 1 90); do
  nvf_port_open "$NVF_FL_PORT" && nvf_port_open "$NVF_ADMIN_PORT" && break
  sleep 1
done
if ! nvf_port_open "$NVF_ADMIN_PORT"; then
  echo "start: NVFlare server ports not open after 90 s" >&2
  tail -20 "$NVF_SERVER_KIT/log.txt" >&2 || true
  exit 1
fi

if nvf_pid_alive "$NVF_ADAPTER_PID"; then
  echo "start: adapter already running (pid $(cat "$NVF_ADAPTER_PID"))"
else
  setsid nohup "$NVF_VENV/bin/python" -m adapters.nvflare.service \
    --port "$NVF_ADAPTER_PORT" --hub-url "$NVF_HUB_URL" --admin-kit "$NVF_ADMIN_KIT" \
    --job-dir "$NVF_JOBS/flhub-relay" --sites "${NVF_SITES// /,}" --base-sites "${NVF_BASE_SITES// /,}" \
    >> "$NVF_LOGS/adapter.log" 2>&1 < /dev/null &
  echo $! > "$NVF_ADAPTER_PID"
  echo "start: adapter starting on 127.0.0.1:$NVF_ADAPTER_PORT (log $NVF_LOGS/adapter.log)"
fi
for _ in $(seq 1 30); do
  nvf_port_open "$NVF_ADAPTER_PORT" && break
  sleep 1
done
nvf_port_open "$NVF_ADAPTER_PORT" || { echo "start: adapter did not open :$NVF_ADAPTER_PORT" >&2; tail -20 "$NVF_LOGS/adapter.log" >&2; exit 1; }
echo "start: OK (sites become online once the hospital clients connect and the relay job runs; see status.sh)"

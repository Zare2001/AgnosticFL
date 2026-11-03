#!/usr/bin/env bash
# Central: removes a TEST NVFlare project completely (the side-by-side distributed provisioning test):
# stops its adapter and server (stop.sh), kills what is left on its ports, deletes $NVF_RUNTIME.
#   NVF_RUNTIME=~/flhub-runtime/nvflare-dp/server NVF_PROJECT=flhub-dp NVF_FL_PORT=8022 \
#   NVF_ADMIN_PORT=8023 NVF_ADAPTER_PORT=8821 bash deploy/central/nvflare/teardown.sh
# Refuses the live project (project flhub or the default runtime dir).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/common.sh"
live="$HOME/flhub-runtime/nvflare/server"
if [ "$NVF_PROJECT" = flhub ] || [ "$(realpath -m "$NVF_RUNTIME")" = "$(realpath -m "$live")" ] \
   || [ "$NVF_FL_PORT" = "$NVFLARE_FL_PORT" ] || [ "$NVF_ADMIN_PORT" = "$NVFLARE_ADMIN_PORT" ]; then
  echo "teardown: refused: $NVF_PROJECT / $NVF_RUNTIME / ports $NVF_FL_PORT,$NVF_ADMIN_PORT is the live project" >&2
  exit 1
fi
bash "$HERE/stop.sh" || true
for port in "$NVF_FL_PORT" "$NVF_ADMIN_PORT" "$NVF_ADAPTER_PORT"; do
  pids=$(ss -Htlnp "sport = :$port" 2>/dev/null | grep -o 'pid=[0-9]*' | cut -d= -f2 | sort -u || true)
  for pid in $pids; do
    # only processes of this test project (their command line or cwd names its runtime dir or port)
    if tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null | grep -q -e "$NVF_RUNTIME" -e "$NVF_ADAPTER_PORT" \
       || [[ "$(readlink "/proc/$pid/cwd" 2>/dev/null)" == "$NVF_RUNTIME"* ]]; then
      kill "$pid" 2>/dev/null && echo "teardown: killed pid $pid on :$port"
    else
      echo "teardown: pid $pid on :$port is not part of $NVF_RUNTIME; left alone" >&2
    fi
  done
done
pkill -f "$NVF_RUNTIME" 2>/dev/null || true
sleep 2
rm -rf "${NVF_RUNTIME:?}"
rmdir "$(dirname "$NVF_RUNTIME")" 2>/dev/null || true
for port in "$NVF_FL_PORT" "$NVF_ADMIN_PORT" "$NVF_ADAPTER_PORT"; do
  nvf_port_open "$port" && echo "teardown: WARNING: :$port still open" >&2
done
echo "teardown: OK: $NVF_PROJECT removed ($NVF_RUNTIME)"

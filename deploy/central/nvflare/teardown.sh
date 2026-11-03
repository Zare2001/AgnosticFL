#!/usr/bin/env bash
# Central: removes a TEST NVFlare project completely (the side-by-side distributed provisioning test):
# stops its adapter and server (stop.sh), kills what is left of it, deletes $NVF_RUNTIME.
#   NVF_RUNTIME=~/flhub-runtime/nvflare-dp/server NVF_PROJECT=flhub-dp NVF_FL_PORT=8022 \
#   NVF_ADMIN_PORT=8023 NVF_ADAPTER_PORT=8821 bash deploy/central/nvflare/teardown.sh
# Only a runtime of the form ~/flhub-runtime/nvflare-<name>/server is accepted; the live project
# (project flhub, ~/flhub-runtime/nvflare/server, its ports) and every ancestor of it are refused.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/common.sh"
rt=$(realpath -m "$NVF_RUNTIME")
live=$(realpath -m "$HOME/flhub-runtime/nvflare/server")
refuse() { echo "teardown: refused: $*" >&2; exit 1; }
[[ "$rt" =~ ^$HOME/flhub-runtime/nvflare-[A-Za-z0-9_]+/server$ ]] \
  || refuse "$rt is not of the form ~/flhub-runtime/nvflare-<name>/server"
[ "$rt" != "$live" ] && [[ "$live/" != "$rt/"* ]] || refuse "$rt is (or contains) the live runtime"
[ "$NVF_PROJECT" != flhub ] || refuse "project flhub is the live project"
for p in "$NVF_FL_PORT" "$NVF_ADMIN_PORT" "$NVF_ADAPTER_PORT"; do
  case "$p" in "$NVFLARE_FL_PORT"|"$NVFLARE_ADMIN_PORT"|"$ADAPTER_NVFLARE_PORT") refuse "port $p is a live port" ;; esac
done

bash "$HERE/stop.sh" || true
# processes of this test project only: command line holds the runtime path (fixed string, not a regex)
# or the adapter's port argument
mine() {
  local pid cmd
  for pid in $(pgrep -u "$(id -u)" -f 'nvflare|adapters\.nvflare' || true); do
    [ "$pid" = "$$" ] && continue
    cmd=$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null) || continue
    if [[ "$cmd" == *"$rt/"* ]] || [[ "$cmd" == *"adapters.nvflare.service --port $NVF_ADAPTER_PORT "* ]] \
       || [[ "$(readlink "/proc/$pid/cwd" 2>/dev/null)" == "$rt"/* ]]; then
      echo "$pid"
    fi
  done
}
pids=$(mine)
[ -z "$pids" ] || { echo "teardown: killing $(echo $pids)"; kill $pids 2>/dev/null || true; sleep 3; }
pids=$(mine)
[ -z "$pids" ] || kill -9 $pids 2>/dev/null || true
rm -rf -- "$rt"
rmdir -- "$(dirname "$rt")" 2>/dev/null || true
for port in "$NVF_FL_PORT" "$NVF_ADMIN_PORT" "$NVF_ADAPTER_PORT"; do
  if nvf_port_open "$port"; then echo "teardown: WARNING: :$port still open" >&2; fi
done
echo "teardown: OK: $NVF_PROJECT removed ($rt)"

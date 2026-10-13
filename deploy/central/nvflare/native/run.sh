#!/usr/bin/env bash
# E7 native baseline on central: NVFlare's built-in FedAvg on the run's NVFlare hospitals, no hub.
#   bash deploy/central/nvflare/native/run.sh configs/stage2/e7/s2-e7-nvflare-hub-<cnn|payload>.yaml <out_dir>
# The hub config of the matching E7 hub run; the native run is named s2-e7-nvflare-native-<cnn|payload>.
# Runs next to the (idle) relay job: the NVFlare server schedules up to 4 jobs and the hospitals
# have no GPU limits, so the relay job needs no pause. The clients get the relay job's custom code
# (clients/nvflare/client.py + flhub_core of this checkout); see adapters/nvflare/native.py.
# Output in <out_dir>: native_config.json, init.safetensors, rounds/rNNNN{.json,.safetensors}
# (+ client weights rNNNN-<site>.safetensors unless payload-*), metrics.jsonl, summary.json, native_job.json.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/../common.sh"
[ $# -eq 2 ] || { echo "usage: $0 <config.yaml> <out_dir>" >&2; exit 2; }
cfg=$(readlink -f "$1") out=$2
[ -e "$out" ] && { echo "native: $out exists" >&2; exit 1; }
nvf_pid_alive "$NVF_SERVER_KIT/daemon_pid.fl" || { echo "native: NVFlare server not running" >&2; exit 1; }
PY="$NVF_VENV/bin/python"
cd "$REPO"
echo "native: $(date -u +%FT%TZ) prepare $cfg -> $out"
"$PY" -m adapters.nvflare.native prepare --config "$cfg" --out "$out"
echo "native: $(date -u +%FT%TZ) submit (waits for the job to end)"
rc=0
"$PY" -m adapters.nvflare.native submit "$out" --admin-kit "$NVF_ADMIN_KIT" --timeout "${NVF_NATIVE_TIMEOUT:-10800}" || rc=$?
"$PY" -m adapters.nvflare.native finalize "$out" || rc=$?
echo "native: $(date -u +%FT%TZ) done (rc $rc): $out"
exit "$rc"

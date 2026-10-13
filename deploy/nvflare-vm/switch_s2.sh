#!/usr/bin/env bash
# One-time switch of the NVFlare hospitals to job-shipped flhub_core (ADR-018). Run it AFTER central
# deployed a relay job whose custom/ carries flhub_core, and after the data variants are provisioned.
#   1. check the stage-2 data files against the manifests (no downtime)
#   2. remove flhub_core from the hospital venv
#   3. restart the clients: stop.sh + start.sh (the running clients still carry the stage-1 editable
#      path in sys.path, which would shadow the job's copy)
#   4. wait until every site's job process reports the job's flhub_core version
# Prints a timeline (steps, wall time, downtime per site) for the RQ2 ledger.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/common.sh"
sites=("$@"); [ ${#sites[@]} -eq 0 ] && read -ra sites <<<"$NVF_SITES"
PY="$NVF_VENV/bin/python"
t0=$(date +%s.%N); ts() { python3 -c "print(f'{$(date +%s.%N) - $t0:7.1f}s')"; }
echo "switch: $(date -u +%FT%TZ) start (sites ${sites[*]})"
nvf_data materialize --variants all --sites "${sites[@]}" --out "$FLHUB_DATA_DIR" \
  --manifests "$REPO/configs/stage2/partitions" > /dev/null
echo "switch: $(ts) 1 data variants verified against the manifests"
before=$(cd / && "$PY" -c 'import flhub_core; print(flhub_core.code_version(), flhub_core.__file__)' 2>/dev/null || echo none)
if [ "$before" != none ]; then uv pip uninstall -q --python "$PY" flhub-core; fi
echo "switch: $(ts) 2 venv flhub_core removed (was: $before)"
declare -A old_jobs  # job dirs before the switch; the version line must come from a new one
for s in "${sites[@]}"; do old_jobs[$s]=" $(ls -d "$NVF_SITES_DIR/$s"/*-*-*-*-*/ 2>/dev/null | tr '\n' ' ')"; done
bash "$HERE/stop.sh" "${sites[@]}" | sed 's/^/switch:   /'
t_down=$(date +%s.%N)
echo "switch: $(ts) 3a clients stopped"
bash "$HERE/start.sh" "${sites[@]}" | sed 's/^/switch:   /'
echo "switch: $(ts) 3b clients started and registered"
for s in "${sites[@]}"; do
  ver=""
  for _ in $(seq 1 300); do
    for d in "$NVF_SITES_DIR/$s"/*-*-*-*-*/; do
      [[ "${old_jobs[$s]}" == *" $d "* || ! -f "$d/log.txt" ]] && continue
      ver=$(grep -o "\[flhub\] $s: .*flhub_core [0-9a-z.+]*" "$d/log.txt" | tail -1 | grep -o 'flhub_core [0-9a-z.+]*' || true)
      [ -n "$ver" ] && break 2
    done
    sleep 1
  done
  [ -n "$ver" ] || { echo "switch: $s did not start a job with flhub_core within 300 s" >&2; exit 1; }
  echo "switch: $(ts) 4 $s job runs $ver (site down for $(python3 -c "print(round($(date +%s.%N) - $t_down, 1))") s)"
done
echo "switch: $(date -u +%FT%TZ) done in $(ts)"

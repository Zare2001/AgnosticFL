#!/usr/bin/env bash
# E6 runs (EXPERIMENTS.md E6), one at a time. Injected runs start only after every ARMED reply.
#   bash scripts/stage2/campaign_e6.sh <step>     steps: a-strict a-minsites b-straggler c d e
set -uo pipefail
cd "$(dirname "$0")/../.."
PY="${PY:-$HOME/venvs/hub/bin/python}"
C=configs/stage2/e6
armed_run() {  # armed_run <name> <arm specs...>
  local name="$1"; shift
  local args=()
  for a in "$@"; do args+=(--arm "$a"); done
  "$PY" scripts/stage2/arm.py --run "$name" "${args[@]}" || { echo "not armed: $name"; return 1; }
  "$PY" scripts/stage2/run_final.py real "$C/$name.yaml"
}
case "${1:-}" in
  a-strict|a-minsites)
    n="s2-e6${1}"
    armed_run "$n" "nvflare:nvf-h1 kill at round 5 +0.5s" "vantage6:v6-h3 kill at round 10 +0.3s" \
      "flower:flw-h5 kill-node at round 15 +4s" ;;
  b-straggler)
    armed_run s2-e6b-straggler "nvflare:nvf-h2 straggler 10% rounds 1-6" \
      "vantage6:v6-h4 straggler 10% rounds 7-13" "flower:flw-h6 straggler 10% rounds 14-20" ;;
  c)
    "$PY" scripts/stage2/run_final.py real "$C/s2-e6c-fedavg.yaml"
    "$PY" scripts/stage2/run_final.py real "$C/s2-e6c-fedavgm.yaml" ;;
  d)
    "$PY" scripts/stage2/inject_central.py e6d --config "$C/s2-e6d-resume.yaml" --round 20 --equal-to s2-e2-dir0.3-fedavgm ;;
  e)
    "$PY" scripts/stage2/inject_central.py e6e --run s2-e6e-server-restart > "$HOME/flhub-runtime/runs/stage2/logs/e6e-inject.log" 2>&1 &
    "$PY" scripts/stage2/run_final.py real "$C/s2-e6e-server-restart.yaml"
    wait ;;
  *) sed -n '2,4p' "$0"; exit 2 ;;
esac

#!/usr/bin/env bash
# E1 then E2 real runs, one at a time (EXPERIMENTS.md §Order step 3). After each real run its
# seed-2027/2028 sims are queued; postproc.py runs them (nice 19, <= 2 workers) once the real run
# passed INV-1 and was finalized. The E1 references are queued first.
#   nohup bash scripts/stage2/campaign_e1e2.sh [e1|e2|all] > ~/flhub-runtime/runs/stage2/logs/campaign_e1e2.log 2>&1 &
set -uo pipefail
cd "$(dirname "$0")/../.."
PY="${PY:-$HOME/venvs/hub/bin/python}"
what="${1:-all}"
run() {  # real run + its seed sims
  local cfg="$1"
  "$PY" scripts/stage2/run_final.py real "$cfg" || echo "FAILED $cfg"
  for seed in 2027 2028; do "$PY" scripts/stage2/run_final.py sim "$cfg" --seed "$seed"; done
}
if [ "$what" = e1 ] || [ "$what" = all ]; then
  "$PY" scripts/stage2/run_final.py reference configs/stage2/e1/s2-e1-centralized.yaml --kind centralized
  "$PY" scripts/stage2/run_final.py reference configs/stage2/e1/s2-e1-local-iid.yaml --kind local
  "$PY" scripts/stage2/run_final.py reference configs/stage2/e1/s2-e1-local-dir0.1.yaml --kind local
  for pid in iid dir1.0 dir0.3 dir0.1 qty fwcorr mix; do run "configs/stage2/e1/s2-e1-$pid-fedavg.yaml"; done
fi
if [ "$what" = e2 ] || [ "$what" = all ]; then
  for pid in iid dir0.3 dir0.1; do
    for agg in fedavgm fedadam fedyogi fedadagrad fedprox; do run "configs/stage2/e2/s2-e2-$pid-$agg.yaml"; done
  done
fi
echo "campaign_e1e2 $what done $(date -u +%FT%TZ)"

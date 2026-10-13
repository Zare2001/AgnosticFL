#!/usr/bin/env bash
# E3 model scale (EXPERIMENTS.md E3), one run at a time, no sims on central during the whole phase
# (HOLD_POST). cnn-medium: INV-1 by full sim afterwards; resnet18-gn: replay spot-check (rounds 1,2,3,last).
#   nohup bash scripts/stage2/campaign_e3.sh > ~/flhub-runtime/runs/stage2/logs/campaign_e3.log 2>&1 &
set -uo pipefail
cd "$(dirname "$0")/../.."
PY="${PY:-$HOME/venvs/hub/bin/python}"
RUNS="$HOME/flhub-runtime/runs/stage2"
echo "E3 phase $(date -u +%FT%TZ)" > "$RUNS/HOLD_POST"
trap 'rm -f "$RUNS/HOLD_POST"' EXIT
for agg in fedavg fedavgm; do
  "$PY" scripts/stage2/run_final.py real "configs/stage2/e3/s2-e3-cnn-medium-$agg.yaml" || echo "FAILED cnn-medium $agg"
done
for agg in fedavg fedavgm; do
  "$PY" scripts/stage2/run_final.py real "configs/stage2/e3/s2-e3-resnet18-gn-$agg.yaml" --replay || echo "FAILED resnet18-gn $agg"
done
echo "campaign_e3 done $(date -u +%FT%TZ)"

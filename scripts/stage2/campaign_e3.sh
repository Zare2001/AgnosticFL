#!/usr/bin/env bash
# E3 model scale (EXPERIMENTS.md E3), one run at a time, no sims on central during the whole phase
# (HOLD_POST). cnn-medium: INV-1 by full sim afterwards; resnet18-gn: replay spot-check (rounds 1,2,3,last).
# The vantage6 store is purged before every run: cnn-medium tasks stay in the server DB (~2.4 GB per run,
# below the adapter's 16 MB per-round delete, ADR-020). After every run the hold is lifted until that run
# is finalized (INV-1 + upload + retention), so the checkpoints of four runs never pile up on the disk.
#   nohup bash scripts/stage2/campaign_e3.sh > ~/flhub-runtime/runs/stage2/logs/campaign_e3.log 2>&1 &
set -uo pipefail
cd "$(dirname "$0")/../.."
PY="${PY:-$HOME/venvs/hub/bin/python}"
RUNS="$HOME/flhub-runtime/runs/stage2"
echo "E3 phase $(date -u +%FT%TZ)" > "$RUNS/HOLD_POST"
trap 'rm -f "$RUNS/HOLD_POST"' EXIT
v6purge() { bash deploy/central/vantage6/purge.sh --vacuum 2>&1 | tail -3; sleep 30; }
drain() {  # drain <name>: post-processing runs (no E3 run active) until <name> is finalized or failed
  rm -f "$RUNS/HOLD_POST"
  until grep -q "^| $1 |" docs/report/stage2/runs.md || ls "$RUNS/queue-failed/"*"-$1.json" >/dev/null 2>&1; do
    sleep 30
  done
  echo "drained $1 $(date -u +%FT%TZ)"
  echo "E3 phase $(date -u +%FT%TZ)" > "$RUNS/HOLD_POST"
}
for agg in fedavg fedavgm; do
  v6purge
  if "$PY" scripts/stage2/run_final.py real "configs/stage2/e3/s2-e3-cnn-medium-$agg.yaml"; then
    drain "s2-e3-cnn-medium-$agg"
  else echo "FAILED cnn-medium $agg"; fi
done
for agg in fedavg fedavgm; do
  v6purge
  if "$PY" scripts/stage2/run_final.py real "configs/stage2/e3/s2-e3-resnet18-gn-$agg.yaml" --replay; then
    drain "s2-e3-resnet18-gn-$agg"
  else echo "FAILED resnet18-gn $agg"; fi
done
echo "campaign_e3 done $(date -u +%FT%TZ)"

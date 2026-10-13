#!/usr/bin/env bash
# E7 overhead vs native FedAvg (EXPERIMENTS.md E7): per framework the native run (the framework
# agent's deploy/central/<fw>/native/run.sh) then the matching hub run, for C1 (cnn) and C2 (payload).
# No sims on central during the phase (HOLD_POST). The native finalize waits for the hub run (postproc).
#   nohup bash scripts/stage2/campaign_e7.sh [nvflare vantage6 flower] > ~/flhub-runtime/runs/stage2/logs/campaign_e7.log 2>&1 &
set -uo pipefail
cd "$(dirname "$0")/../.."
PY="${PY:-$HOME/venvs/hub/bin/python}"
RUNS="$HOME/flhub-runtime/runs/stage2"
echo "E7 phase $(date -u +%FT%TZ)" > "$RUNS/HOLD_POST"
trap 'rm -f "$RUNS/HOLD_POST"' EXIT
fws=("${@:-nvflare vantage6 flower}")
for fw in ${fws[@]}; do
  for c in cnn payload; do
    cfg="configs/stage2/e7/s2-e7-$fw-hub-$c.yaml"
    "$PY" scripts/stage2/run_final.py native "$fw" "$cfg" --hub-run "s2-e7-$fw-hub-$c" --name "s2-e7-$fw-native-$c" \
      || echo "FAILED native $fw $c"
    "$PY" scripts/stage2/run_final.py real "$cfg" || echo "FAILED hub $fw $c"
    if [ "$fw" = vantage6 ] && [ "$c" = payload ]; then bash deploy/central/vantage6/purge.sh --vacuum || true; fi
    [ "$c" = payload ] && [ -x "deploy/central/$fw/purge.sh" ] && [ "$fw" != vantage6 ] && bash "deploy/central/$fw/purge.sh" || true
  done
done
echo "campaign_e7 done $(date -u +%FT%TZ)"

#!/usr/bin/env bash
# P3 final runs on the central VM, online to W&B (tag final, one group), then the equivalence check.
#   bash scripts/run_final.sh all            # all six runs + equivalence, in order
#   bash scripts/run_final.sh <step> ...     # steps: centralized sim nvflare vantage6 flower mixed equivalence
# A failed or superseded run must be deleted from W&B (scripts/wandb_cleanup.py) before re-running it.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"
PY="${HUB_VENV:-$HOME/venvs/hub}/bin/python"
RUNS="$HOME/flhub-runtime/runs"
CONFIG=configs/final.yaml
GROUP_FILE="$HOME/flhub-runtime/final_group"
[ -f "$GROUP_FILE" ] || echo "final-$(date -u +%F)" > "$GROUP_FILE"
GROUP="${FINAL_GROUP:-$(cat "$GROUP_FILE")}"
COMMON=(--wandb online --tags final --group "$GROUP")
export WANDB_MODE=online

step() {
  echo "=== $1 ($(date -u +%FT%TZ), group $GROUP)"
  case "$1" in
    centralized) "$PY" -m hub.sim --config "$CONFIG" --mode centralized --name centralized-baseline "${COMMON[@]}" ;;
    sim)         "$PY" -m hub.sim --config "$CONFIG" --name sim-fedavg-reference "${COMMON[@]}" ;;
    nvflare|vantage6|flower)
                 "$PY" -m hub.orchestrator run --config "$CONFIG" --only "$1" --name "$1-only" "${COMMON[@]}" ;;
    mixed)       "$PY" -m hub.orchestrator run --config "$CONFIG" --name mixed-6sites "${COMMON[@]}" ;;
    equivalence)
      local id path
      id=$("$PY" -c "import json; print(json.load(open('$RUNS/mixed-6sites-latest/summary.json'))['wandb_id'])")
      path=$("$PY" -c "import os, wandb; print(f\"{os.environ.get('WANDB_ENTITY') or wandb.Api().default_entity}/{os.environ.get('WANDB_PROJECT', 'AgnosticFL')}/$id\")")
      "$PY" scripts/equivalence_check.py "$RUNS/mixed-6sites-latest" "$RUNS/sim-fedavg-reference-latest" \
        --json "$RUNS/equivalence-final.json" --wandb-run "$path" ;;
    *) echo "unknown step $1" >&2; exit 2 ;;
  esac
}

[ $# -gt 0 ] || { sed -n '2,5p' "$0"; exit 2; }
if [ "$1" = all ]; then set -- centralized sim nvflare vantage6 flower mixed equivalence; fi
for s in "$@"; do step "$s"; done

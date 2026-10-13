#!/usr/bin/env bash
# E7 native baseline for Flower, run by central (EXPERIMENTS.md E7):
#   bash deploy/central/flower/native/run.sh configs/stage2/<run>.yaml <out_dir>
# Stages a FAB from this checkout (the native ServerApp flhub_native + the hospitals' standard
# ClientApp flhub_flower + flhub_core), submits it with `flwr run` to the local SuperLink as a run
# of its own, waits until Flower reports the run finished, and keeps Flower's own run record
# (status, network bytes, compute seconds) and the ServerApp log next to the hub-format outputs:
#   <out_dir>/ config.json metrics.jsonl rounds/ events.jsonl summary.json flwr_run.json serverapp.log
# The hub's relay run stays up and idle; it need not be paused (two runs share the SuperLink and
# the SuperNodes; no hub round may run at the same time, as for every final run).
# Exit 0 only if Flower says finished:completed and summary.json says finished.
set -euo pipefail
NATIVE="$(cd "$(dirname "$0")" && pwd)"
. "$NATIVE/../common.sh"
config="$(realpath "${1:?usage: run.sh <config.yaml> <out_dir>}")"
out="$(realpath -m "${2:?usage: run.sh <config.yaml> <out_dir>}")"
[ ! -e "$out/metrics.jsonl" ] || { echo "$out already holds a run" >&2; exit 1; }
mkdir -p "$out"

stage="$RUNTIME/native-fab-src"
rm -rf "$stage"; mkdir -p "$stage"
cp "$NATIVE/pyproject.toml" "$stage/"
cp -r "$NATIVE/flhub_native" "$REPO/clients/flower/flhub_flower" "$REPO/flhub_core/src/flhub_core" "$stage/"
find "$stage" -name __pycache__ -prune -exec rm -rf {} +

export FLWR_HOME="$FLWR_HOME_DIR"
t0=$(date +%s)
sub="$(flwr run "$stage" flhub --run-config "config='$config' out-dir='$out'" 2>&1)" || { echo "$sub" >&2; exit 1; }
run_id="$(echo "$sub" | grep -oiE 'started run [0-9]+' | grep -oE '[0-9]+' | head -1 || true)"
[ -n "$run_id" ] || { echo "flwr run returned no run id: $sub" >&2; exit 1; }
echo "$(date -u +%FT%TZ) native run $run_id submitted: $(basename "$config") -> $out"

limit="${FLHUB_NATIVE_MAX_S:-10800}"
status=unknown
while :; do
  sleep 5
  status="$(flwr ls flhub --run-id "$run_id" --format json 2>/dev/null | "$VENV/bin/python" -c \
            'import json, sys; print(json.load(sys.stdin)["runs"][0]["status"])' 2>/dev/null || echo unknown)"
  case "$status" in finished*) break ;; esac
  if [ $(( $(date +%s) - t0 )) -gt "$limit" ]; then
    echo "native run $run_id still '$status' after ${limit}s: stopping it" >&2
    flwr stop "$run_id" flhub >&2 || true
    break
  fi
done
flwr ls flhub --run-id "$run_id" --format json > "$out/flwr_run.json" 2>&1 || true
flwr log "$run_id" flhub --show > "$out/serverapp.log" 2>&1 || true
echo "$(date -u +%FT%TZ) native run $run_id: $status after $(( $(date +%s) - t0 )) s"
"$VENV/bin/python" - "$out" "$status" <<'PY'
import json, sys
from pathlib import Path
out, status = Path(sys.argv[1]), sys.argv[2]
summary = json.loads((out / "summary.json").read_text()) if (out / "summary.json").exists() else {"status": "missing"}
print(json.dumps({k: summary.get(k) for k in ("status", "rounds", "final/global_ref", "round_time_median_s",
                                              "final/test_acc", "error") if k in summary}))
sys.exit(0 if status == "finished:completed" and summary.get("status") == "finished" else 1)
PY

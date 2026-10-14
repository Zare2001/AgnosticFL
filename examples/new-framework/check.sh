#!/usr/bin/env bash
# Runs the adapter conformance suite against both skeletons (adapter.py, relay.py) with a
# temporary hub core. Everything runs on free local ports and in a temporary directory.
#
#   bash examples/new-framework/check.sh        (in the virtual environment of docs/getting-started.md)
#
# The toy hospitals and the suite read the site data from $FLHUB_DATA_DIR (default ~/flhub-data)
# and build it there on first use, as the mock adapter does.
set -euo pipefail
cd "$(dirname "$0")/../.."
REPO=$(pwd -P)
EX="$REPO/examples/new-framework"
W=$(mktemp -d /tmp/flhub-new-framework.XXXXXX)
SITES=(nvf-h1 nvf-h2)  # benchmark sites: the suite trains their partitions directly for reference
OFFLINE=(toy-h7)  # a registered site that is not connected
VARIANT=${FLHUB_VARIANT:-}  # e.g. organamnist-dir0.3: also run the data-variant test

port() { python -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])'; }
up() {  # wait until a service answers /health (first start may build the site data)
  for _ in $(seq 600); do curl -sf "$1/health" > /dev/null && return 0; sleep 0.5; done
  echo "no answer from $1" >&2; return 1
}
start() {  # start <name> <command...>: run in the background, keep the pid
  "${@:2}" > "$W/$1.log" 2>&1 &
  echo $! > "$W/$1.pid"
}
stop() { kill "$(cat "$W/$1.pid")" 2> /dev/null || true; rm -f "$W/$1.pid"; }
cleanup() {
  for p in "$W"/*.pid; do [ -e "$p" ] && { kill "$(cat "$p")" 2> /dev/null || true; }; done
  rm -rf "$W"
}
trap cleanup EXIT

if [ -n "$VARIANT" ]; then  # build that partition for the sites first (downloads the dataset once)
  python -m flhub_core.data materialize --sites "${SITES[@]}" --variants "$VARIANT"
fi
HUB=http://127.0.0.1:$(port)
start hubcore python -m hub.core --port "${HUB##*:}" --root "$W/hubcore"
up "$HUB"

conformance() {  # conformance <name> <adapter url> <restart script>
  echo "== $1: pytest tests/conformance --adapter $2"
  python -m pytest tests/conformance -p no:cacheprovider --adapter "$2" --hub "$HUB" --sites "${SITES[@]}" \
    --restart-cmd "bash $3" ${VARIANT:+--variant "$VARIANT"} -rs || return 1
}

# direct pattern: one process, the adapter calls the framework
D=http://127.0.0.1:$(port)
DIRECT=(python "$EX/adapter.py" --port "${D##*:}" --hub "$HUB" --sites "${SITES[@]}" --offline "${OFFLINE[@]}")
cat > "$W/restart-direct.sh" << EOF
kill \$(cat $W/direct.pid); while kill -0 \$(cat $W/direct.pid) 2> /dev/null; do sleep 0.2; done
cd $REPO && ${DIRECT[*]} > $W/direct.log 2>&1 & echo \$! > $W/direct.pid
until curl -sf $D/health > /dev/null; do sleep 0.5; done
EOF
start direct "${DIRECT[@]}"
up "$D"
status=0
conformance direct "$D" "$W/restart-direct.sh" || status=1

# the unchanged orchestrator runs three rounds through the direct skeleton; INV-1 against hub.sim
echo "== orchestrator: 3 rounds through the skeleton, then hub.sim --compare"
sed -e "s#http://127.0.0.1:8800#$HUB#" -e "s#http://127.0.0.1:8820#$D#" "$EX/run.yaml" > "$W/run.yaml"
python -m hub.orchestrator run --config "$W/run.yaml" --runs-dir "$W/runs" > "$W/orchestrator.out" 2>&1 || status=1
grep -E '"status"|"final/test_acc"' "$W/orchestrator.out" || true
grep -q '"status": "finished"' "$W/orchestrator.out" || status=1
python -m hub.sim --config "$W/run.yaml" --runs-dir "$W/runs" --compare "$W/runs/toy-3r-latest" > "$W/sim.out" 2>&1 || status=1
grep "INV-1" "$W/sim.out" || true
grep -q "hashes_equal=True" "$W/sim.out" || status=1
stop direct

# relay pattern: the adapter service plus the relay inside the (toy) framework server
R=http://127.0.0.1:$(port)
ADAPTER=(python "$EX/relay.py" adapter --port "${R##*:}" --hub "$HUB" --sites "${SITES[@]}" --offline "${OFFLINE[@]}")
cat > "$W/restart-relay.sh" << EOF
kill \$(cat $W/relay-adapter.pid); while kill -0 \$(cat $W/relay-adapter.pid) 2> /dev/null; do sleep 0.2; done
cd $REPO && ${ADAPTER[*]} > $W/relay-adapter.log 2>&1 & echo \$! > $W/relay-adapter.pid
until curl -sf $R/health > /dev/null; do sleep 0.5; done
EOF
start relay-adapter "${ADAPTER[@]}"
up "$R"
start relay python "$EX/relay.py" relay --adapter "$R" --hub "$HUB" --sites "${SITES[@]}" --offline "${OFFLINE[@]}"
conformance relay "$R" "$W/restart-relay.sh" || status=1

if [ "$status" = 0 ]; then
  echo "== both skeletons pass the conformance suite"
else
  tail -n 20 "$W"/*.log >&2
  echo "== FAILED" >&2
fi
exit "$status"

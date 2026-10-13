#!/usr/bin/env bash
# Continuous system monitoring on the vantage6 VM during stage 2 (PLAN.md §4.3): scripts/sysmon.py
# with one component per hospital node process (vnode-local ... -n <site>) plus the stock components
# (v6-algorithm = the training containers of all nodes, docker, ...).
#   bash deploy/vantage6-vm/sysmon.sh start|stop|status
#   bash deploy/vantage6-vm/sysmon.sh summary <run name> <from UTC> <to UTC>   # cut + send to central
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
DIR="${FLHUB_SYSMON_DIR:-$HOME/flhub-runtime/sysmon}"
PIDF="$DIR/sysmon.pid"
mkdir -p "$DIR"
pid_alive() { [ -f "$1" ] && kill -0 "$(cat "$1")" 2>/dev/null; }
procs=()
for site in v6-h3 v6-h4 v6-h9 v6-h10; do
  procs+=("$site-node=vnode-local start .* -n $site( |\$)")
done

case "${1:-}" in
  start)
    if pid_alive "$PIDF"; then echo "sysmon: already running (pid $(cat "$PIDF"))"; exit 0; fi
    setsid nohup /usr/bin/python3 "$REPO/scripts/sysmon.py" run --dir "$DIR" --proc "${procs[@]}" \
        >> "$DIR/sysmon.log" 2>&1 < /dev/null &
    echo $! > "$PIDF"
    echo "sysmon: started (pid $!, dir $DIR)"
    ;;
  stop)
    if pid_alive "$PIDF"; then kill "$(cat "$PIDF")"; echo "sysmon: stopped"; else echo "sysmon: not running"; fi
    rm -f "$PIDF"
    ;;
  status)
    pid_alive "$PIDF" && echo "sysmon: running (pid $(cat "$PIDF"))" || echo "sysmon: NOT running"
    /usr/bin/python3 "$REPO/scripts/sysmon.py" status --dir "$DIR"
    ;;
  summary)
    run="${2:?run name}"; from="${3:?from UTC}"; to="${4:?to UTC}"
    out="$DIR/summaries/sysmon-vantage6-$run.json"
    mkdir -p "$DIR/summaries"
    /usr/bin/python3 "$REPO/scripts/sysmon.py" summarize --dir "$DIR" --from "$from" --to "$to" --run "$run" -o "$out"
    "$REPO/tools/bus" put "$out" --to central
    ;;
  *) echo "usage: $0 start|stop|status | summary <run> <from UTC> <to UTC>" >&2; exit 2 ;;
esac

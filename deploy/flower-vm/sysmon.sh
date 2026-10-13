#!/usr/bin/env bash
# Continuous system monitoring on the flower VM during stage 2 (PLAN.md §4.3): scripts/sysmon.py
# with per-site components for the hospital SuperNodes (flower-supernode, flower-superexec and the
# per-message flwr-clientapp, told apart by their local runtime port), plus the stock components.
#   bash deploy/flower-vm/sysmon.sh start|stop|status
#   bash deploy/flower-vm/sysmon.sh summary <run name> <from UTC> <to UTC>   # cut + send to central
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
. "$REPO/deploy/topology.env"
DIR="${FLHUB_SYSMON_DIR:-$HOME/flhub-runtime/sysmon}"
PIDF="$DIR/sysmon.pid"
mkdir -p "$DIR"
pid_alive() { [ -f "$1" ] && kill -0 "$(cat "$1")" 2>/dev/null; }

procs=()
for entry in flw-h5:9094 flw-h6:9095 flw-h11:9096 flw-h12:9097; do
  site="${entry%%:*}"; port="${entry##*:}"
  procs+=("$site-supernode=flower-supernode .*--superlink ${FLHUB_CENTRAL_IP//./\\.}:$FLOWER_FLEET_PORT .*site-id='$site'"
          "$site-superexec=flower-superexec .*127\\.0\\.0\\.1:$port( |\$)"
          "$site-clientapp=flwr-clientapp .*127\\.0\\.0\\.1:$port( |\$)")
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
    out="$DIR/summaries/sysmon-flower-$run.json"
    mkdir -p "$DIR/summaries"
    /usr/bin/python3 "$REPO/scripts/sysmon.py" summarize --dir "$DIR" --from "$from" --to "$to" --run "$run" -o "$out"
    "$REPO/tools/bus" put "$out" --to central
    ;;
  *) echo "usage: $0 start|stop|status | summary <run> <from UTC> <to UTC>" >&2; exit 2 ;;
esac

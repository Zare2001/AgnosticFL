#!/usr/bin/env bash
# Runs scripts/sysmon.py on the NVFlare VM through S4: sysmon.sh {start|stop|status|send <run> <from> <to>}
# Per-site components (client process, job process) for the real hospitals; island processes
# (~/flhub-island) are kept apart so they never count as NVFlare hospitals.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/common.sh"
DIR="${SYSMON_DIR:-$HOME/flhub-runtime/sysmon}"
procs=("island=flhub-island/(server|sites|hubcore)" "inject=inject_watch\.py")  # pattern must not match its own text
for s in nvf-h1 nvf-h2 nvf-h7 nvf-h8; do
  procs+=("$s-client=client_train -m \S+/sites/$s( |$)" "$s-job=worker_process -m \S+/sites/$s( |$)")
done
running() { pgrep -f "^python3 scripts/sysmon\.py run" >/dev/null; }  # ^: not the calling shell
case "${1:-status}" in
  start)
    if running; then echo "sysmon: already running"; else
      mkdir -p "$DIR"
      (cd "$REPO" && setsid nohup python3 scripts/sysmon.py run --dir "$DIR" --proc "${procs[@]}" \
        >> "$DIR/sysmon.out" 2>&1 < /dev/null &)
      sleep 2; running && echo "sysmon: started ($DIR)" || { echo "sysmon: failed, see $DIR/sysmon.out" >&2; exit 1; }
    fi ;;
  stop) pkill -f "^python3 scripts/sysmon\.py run" && echo "sysmon: stopped" || echo "sysmon: not running" ;;
  status) running && echo "sysmon: running" || echo "sysmon: NOT running"; python3 "$REPO/scripts/sysmon.py" status --dir "$DIR" ;;
  send)  # sysmon.sh send <run name> <RUN start UTC> <RUN end UTC>: window summary -> central
    [ $# -eq 4 ] || { echo "usage: $0 send <run> <from UTC> <to UTC>" >&2; exit 2; }
    out="$DIR/summaries/$2-nvflare-sysmon.json"; mkdir -p "$DIR/summaries"
    python3 "$REPO/scripts/sysmon.py" summarize --dir "$DIR" --from "$3" --to "$4" --run "$2" -o "$out"
    "$REPO/tools/bus" put "$out" --to central ;;
  *) echo "usage: $0 {start|stop|status|send <run> <from> <to>}" >&2; exit 2 ;;
esac

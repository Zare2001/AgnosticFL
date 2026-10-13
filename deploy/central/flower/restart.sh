#!/usr/bin/env bash
# Restart one part of the Flower server side on central (idempotent start afterwards).
#   bash deploy/central/flower/restart.sh adapter            # rollout: new FAB (flhub_core) + new relay run;
#                                                            # SuperLink and hospitals stay connected
#   bash deploy/central/flower/restart.sh superlink [--kill] # E6e: SIGTERM (or SIGKILL) the SuperLink, start it
#                                                            # again; the adapter relaunches the relay run
# Prints UTC times of the stop and of the server side being HEALTHY again.
set -euo pipefail
. "$(dirname "$0")/common.sh"
part="${1:-}"; sig=TERM
[ "${2:-}" = "--kill" ] && sig=KILL
case "$part" in
  adapter) pidf="$RUNTIME/run/adapter.pid"; grace=30 ;;
  superlink) pidf="$RUNTIME/run/superlink.pid"; grace=15 ;;
  *) echo "usage: $0 adapter | superlink [--kill]" >&2; exit 2 ;;
esac
relay_run() {  # run id of the relay the adapter currently hears from (empty if none)
  curl -sf "http://127.0.0.1:$ADAPTER_PORT/relay/status" | "$VENV/bin/python" -c \
    'import json,sys; d=json.load(sys.stdin); print((d.get("relay") or {}).get("run_id") or "" if d.get("relay_alive") else "")' \
    2>/dev/null || true
}
before="$(relay_run)"
t0=$(date +%s.%N)
if pid_alive "$pidf"; then
  pid="$(cat "$pidf")"
  echo "$(date -u +%FT%T.%3NZ) $part: SIG$sig pid $pid"
  kill "-$sig" "$pid"
  for _ in $(seq "$((grace * 2))"); do kill -0 "$pid" 2>/dev/null || break; sleep 0.5; done
  kill -0 "$pid" 2>/dev/null && kill -9 "$pid"
fi
bash "$(dirname "$0")/start.sh"
for _ in $(seq 180); do
  now_run="$(relay_run)"
  if [ -n "$now_run" ] && [ "$now_run" != "$before" ] && bash "$(dirname "$0")/status.sh" >/dev/null 2>&1; then
    echo "$(date -u +%FT%T.%3NZ) relay run $before -> $now_run"
    echo "$(date -u +%FT%T.%3NZ) flower central HEALTHY again after $(awk "BEGIN{printf \"%.1f\", $(date +%s.%N) - $t0}") s"
    exit 0
  fi
  sleep 1
done
echo "flower central not HEALTHY with a new relay run 180 s after the restart" >&2
bash "$(dirname "$0")/status.sh" || true
exit 1

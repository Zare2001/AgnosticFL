#!/usr/bin/env bash
# Starts NVFlare hospital clients with their standard startup kits: start.sh [site ...]
# (default: the NVFlare sites of deploy/topology.env). Marks each site enabled for the watchdog.
#   start.sh --watchdog   (cron, every minute) starts every enabled site that is not running
# Hospital-side settings: the kit (server address + certificates) and the data file path.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/common.sh"
export PATH="$NVF_VENV/bin:$PATH"
watchdog=0
if [ "${1:-}" = --watchdog ]; then watchdog=1; shift; fi
mkdir -p "$NVF_SITES_DIR"
exec 9>"$NVF_SITES_DIR/.start.lock"
if [ "$watchdog" = 1 ]; then
  flock -n 9 || exit 0  # a start or stop is in progress
  read -ra sites <<<"$(nvf_enabled_sites | tr '\n' ' ')"
  [ ${#sites[@]} -gt 0 ] || { nvf_watchdog_sync; exit 0; }
else
  flock -w 180 9 || { echo "start: another start.sh/stop.sh holds the lock" >&2; exit 1; }
  sites=("$@"); [ ${#sites[@]} -eq 0 ] && read -ra sites <<<"$NVF_SITES"
fi
declare -A log_from
started=()

for site in "${sites[@]}"; do
  ws="$NVF_SITES_DIR/$site"
  [ -d "$ws/startup" ] || { echo "start: no startup kit for $site in $ws (run install.sh <kit.tgz>)" >&2; exit 1; }
  [ "$watchdog" = 1 ] || touch "$ws/.enabled"
  if nvf_pid_alive "$ws/daemon_pid.fl"; then
    [ "$watchdog" = 1 ] || echo "start: $site already running"
    continue
  fi
  rm -f "$ws/shutdown.fl"
  lines=0
  if [ -f "$ws/log.txt" ]; then lines=$(wc -l < "$ws/log.txt"); fi
  log_from[$site]=$((lines + 1))
  # neither the client nor the launching subshell may keep the start.sh lock (fd 9) or our stdout
  (exec 9>&- > "$ws/start.out" 2>&1 < /dev/null
   cd "$ws" && FLHUB_DATA_PATH="$FLHUB_DATA_DIR/$site.csv" setsid nohup bash startup/start.sh &)
  started+=("$site")
  echo "start: $(date -u +%FT%TZ) $site starting (data $FLHUB_DATA_DIR/$site.csv)"
done

for site in "${started[@]}"; do
  ws="$NVF_SITES_DIR/$site"
  registered() {  # one awk instead of tail | grep -q, which can fail on SIGPIPE under pipefail
    awk -v from="${log_from[$site]}" 'NR >= from && /Successfully registered client/ {ok = 1} END {exit !ok}' \
      "$ws/log.txt" 2>/dev/null
  }
  for _ in $(seq 1 60); do registered && break; sleep 1; done
  if registered; then
    echo "start: $site registered with the NVFlare server"
  else
    echo "start: $site not registered after 60 s; see $ws/log.txt" >&2
    exit 1
  fi
done
nvf_watchdog_sync
[ "$watchdog" = 1 ] || echo "start: OK (watchdog: cron restarts enabled sites every minute until stop.sh)"

#!/usr/bin/env bash
# Starts the NVFlare hospital clients with their standard startup kits: start.sh [site ...]
# Hospital-side settings: the kit (server address + certificates) and the data file path.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/common.sh"
export PATH="$NVF_VENV/bin:$PATH"
mkdir -p "$NVF_SITES_DIR"
exec 9>"$NVF_SITES_DIR/.start.lock"
flock -n 9 || { echo "start: another start.sh is running"; exit 0; }
sites=("$@"); [ ${#sites[@]} -eq 0 ] && read -ra sites <<<"$NVF_SITES"
declare -A log_from

for site in "${sites[@]}"; do
  ws="$NVF_SITES_DIR/$site"
  log_from[$site]=1
  [ -d "$ws/startup" ] || { echo "start: no startup kit for $site in $ws (run install.sh <kit.tgz>)" >&2; exit 1; }
  if nvf_pid_alive "$ws/daemon_pid.fl"; then
    echo "start: $site already running"
    continue
  fi
  rm -f "$ws/shutdown.fl"
  lines=0
  if [ -f "$ws/log.txt" ]; then lines=$(wc -l < "$ws/log.txt"); fi
  log_from[$site]=$((lines + 1))
  # 9>&-: the client must not inherit the start.sh lock
  (cd "$ws" && FLHUB_DATA_PATH="$FLHUB_DATA_DIR/$site.csv" setsid nohup bash startup/start.sh \
    > "$ws/start.out" 2>&1 < /dev/null 9>&- &)
  echo "start: $site starting (data $FLHUB_DATA_DIR/$site.csv)"
done

for site in "${sites[@]}"; do
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
nvf_watchdog_on
echo "start: OK (watchdog: cron re-runs start.sh every minute until stop.sh)"

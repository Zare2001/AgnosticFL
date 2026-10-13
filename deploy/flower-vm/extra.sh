#!/usr/bin/env bash
# E5 extra hospitals flw-h11 and flw-h12 on the flower VM (EXPERIMENTS.md E5): the same standard
# flower-supernode, start/stop/status scripts and supervisor as flw-h5/flw-h6, with their own
# runtime dir, local runtime ports and watchdog cron line. They run only during E5.
#   bash deploy/flower-vm/extra.sh start|stop|status
# Data: organamnist-iid12-<site>.csv in ~/flhub-data (data-path ~/flhub-data/<site>.csv; no
# stage-1 file exists for them, so a run must set train_config data_variant).
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
export FLHUB_FLOWER_RUNTIME="${FLHUB_FLOWER_E5_RUNTIME:-$HOME/flhub-runtime/flower-e5}"
export FLHUB_FLOWER_NODES="${FLHUB_FLOWER_E5_NODES:-flw-h11:9096 flw-h12:9097}"
. "$REPO/deploy/flower-vm/common.sh"

watchdog_line() {  # cron entry like install.sh's, for this runtime dir and node list
  echo "* * * * * [ -f $RUNTIME/nodes.enabled ] && FLHUB_SUPERLINK=\"\$(cat $RUNTIME/nodes.enabled)\"" \
       "FLHUB_FLOWER_RUNTIME=$RUNTIME FLHUB_FLOWER_NODES=\"$NODES\" FLHUB_WATCHDOG=1" \
       "flock -n $RUNTIME/watchdog.lock bash $REPO/deploy/flower-vm/start.sh >> $RUNTIME/logs/watchdog.log 2>&1"
}

case "${1:-}" in
  start)
    bash "$REPO/deploy/flower-vm/start.sh"
    others="$(crontab -l 2>/dev/null | grep -vF "FLHUB_FLOWER_RUNTIME=$RUNTIME " || true)"
    printf '%s\n%s\n' "$others" "$(watchdog_line)" | sed '/^$/d' | crontab -
    echo "watchdog: cron entry for $RUNTIME installed"
    ;;
  stop)
    bash "$REPO/deploy/flower-vm/stop.sh"   # removes nodes.enabled: the cron entry stays idle
    crontab -l 2>/dev/null | grep -vF "FLHUB_FLOWER_RUNTIME=$RUNTIME " | crontab -
    echo "watchdog: cron entry for $RUNTIME removed"
    ;;
  status) bash "$REPO/deploy/flower-vm/status.sh" ;;
  *) echo "usage: $0 start|stop|status" >&2; exit 2 ;;
esac

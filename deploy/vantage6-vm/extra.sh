#!/usr/bin/env bash
# E5 extra hospitals v6-h9/v6-h10 on the vantage6 VM: standard vantage6 nodes like v6-h3/v6-h4,
# with only their organamnist-iid12 data. They run only during E5; registered but offline they
# block nothing (flhub/0.2 readiness per run).
#   install [--keys FILE]  node configs from the operator's API keys (central add_sites.sh, bus --secret)
#   start                  start both nodes, wait until connected, add their watchdog cron entry
#   stop                   stop both nodes and remove their watchdog cron entry
#   status                 exit 0 = both running and connected
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
. "$REPO/deploy/topology.env"
. "$REPO/deploy/versions.env"
. "$REPO/deploy/vantage6-vm/common.sh"
SITES="v6-h9 v6-h10"
RT="$HOME/flhub-runtime/vantage6"
VENV="$HOME/venvs/v6"
DATA_VENV="$HOME/venvs/flhub-data"
KEYS="$RT/node-keys-extra.env"
CRON="* * * * * [ -f $RT/extra-nodes.enabled ] && flock -n $RT/watchdog-extra.lock bash $REPO/deploy/vantage6-vm/extra.sh start >> $RT/watchdog-extra.log 2>&1"
log() { echo "[v6-vm-extra] $*"; }
cron_set() {  # cron_set on|off: the extra nodes' watchdog entry
  local others; others="$(crontab -l 2>/dev/null | grep -v 'deploy/vantage6-vm/extra.sh start' || true)"
  if [ "$1" = on ]; then printf '%s\n%s\n' "$others" "$CRON" | sed '/^$/d' | crontab -
  else printf '%s\n' "$others" | sed '/^$/d' | crontab -; fi
}

case "${1:-}" in
  install)
    if [ "${2:-}" = "--keys" ]; then install -m 600 "${3:?keys file}" "$KEYS" && rm -f "$3"; fi
    [ -f "$KEYS" ] || { echo "no $KEYS: pass --keys FILE (operator, bus --secret)" >&2; exit 1; }
    PYTHONPATH="$REPO/flhub_core/src" "$DATA_VENV/bin/python" -m flhub_core.data materialize --out "$V6_DATA_DIR" \
      --variants organamnist-iid12 --sites $SITES --manifests "$REPO/configs/stage2/partitions" | sed 's/^/[v6-vm-extra] data /'
    set -a; . "$KEYS"; set +a
    allowed="^$(echo "$FLHUB_CENTRAL_IP:$REGISTRY_PORT" | sed 's/\./\\./g')/flhub/v6-train(:[A-Za-z0-9._-]+)?(@sha256:[a-f0-9]{64})?\$"
    for s in $SITES; do
      var="V6_API_KEY_$(echo "$s" | tr 'a-z-' 'A-Z_')"
      [ -n "${!var:-}" ] || { echo "$KEYS has no $var" >&2; exit 1; }
      render_node_config "$s" "$s" "${!var}" "http://$FLHUB_CENTRAL_IP" "$allowed"
      log "node config ~/.config/vantage6/node/$s.yaml ($(node_databases "$s" | grep -c label) database)"
    done
    ;;
  start)
    touch "$RT/extra-nodes.enabled"
    for s in $SITES; do
      [ -f "$HOME/.config/vantage6/node/$s.yaml" ] || { echo "no node config for $s: run extra.sh install" >&2; exit 1; }
      if [ -z "$(docker ps -q --filter "name=^vantage6-$s-user$")" ]; then
        (cd "$HOME" && "$VENV/bin/v6" node start -n "$s" --user >/dev/null)
        log "started node $s"
      fi
    done
    for s in $SITES; do wait_connected "$s"; log "node $s connected"; done
    crontab -l 2>/dev/null | grep -q 'deploy/vantage6-vm/extra.sh start' || cron_set on
    ;;
  stop)
    rm -f "$RT/extra-nodes.enabled"
    cron_set off
    for s in $SITES; do
      ids="$(docker ps -q --filter "name=^vantage6-$s-user$")"
      if [ -n "$ids" ]; then docker stop $ids >/dev/null; log "stopped node $s"; fi
    done
    log "extra nodes stopped, watchdog entry removed"
    ;;
  status)
    ok=0
    for s in $SITES; do
      if [ -z "$(docker ps -q --filter "name=^vantage6-$s-user$")" ]; then echo "$s  DOWN"; ok=1; continue; fi
      last="$(docker logs "vantage6-$s-user" 2>&1 | grep -E 'Connected to the /tasks|Disconnected from the server' | tail -1)"
      case "$last" in *Connected*) echo "$s  OK    connected" ;; *) echo "$s  DISCONNECTED"; ok=1 ;; esac
    done
    echo "watchdog entry: $(crontab -l 2>/dev/null | grep -c 'deploy/vantage6-vm/extra.sh start')"
    exit $ok
    ;;
  *) echo "usage: $0 install [--keys FILE] | start | stop | status" >&2; exit 2 ;;
esac

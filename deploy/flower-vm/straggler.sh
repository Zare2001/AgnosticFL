#!/usr/bin/env bash
# E6b straggler on the flower VM: throttle ONE hospital SuperNode's CPU (EXPERIMENTS.md E6b).
#   bash deploy/flower-vm/straggler.sh prepare <site>     restart it inside its scope, no limit yet
#   bash deploy/flower-vm/straggler.sh on <site> <cpu%>   e.g. on flw-h5 10
#   bash deploy/flower-vm/straggler.sh off <site>         remove the limit (no restart)
#   bash deploy/flower-vm/straggler.sh release <site>     restart the SuperNode normally (end state)
#   bash deploy/flower-vm/straggler.sh status
# `prepare` (or the first `on`) restarts the site's supervisor (supernode.sh) inside the user systemd
# scope flhub-straggler-<site>.scope (new SuperNode id; the relay maps it within ~15 s, so do it before
# the run). Every process of that SuperNode, including each per-message flwr-clientapp, inherits the
# scope's cgroup. Later on/off only change CPUQuota (systemctl set-property, instant), e.g. armed
# with inject.sh straggler-on:<cpu%> at a hub round.
# The watchdog lock is held while the supervisor is swapped, so the cron watchdog never interferes.
set -euo pipefail
. "$(dirname "$0")/common.sh"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
DIR="$RUNTIME/e6"
mkdir -p "$DIR"
now() { date -u +%Y-%m-%dT%H:%M:%S.%2NZ; }
log() { echo "$(now) $*" | tee -a "$DIR/straggler.log"; }
unit() { echo "flhub-straggler-$1.scope"; }
in_scope() {  # the site's supervisor runs inside its straggler scope
  local pidf="$RUNTIME/run/supernode-$1.pid"
  pid_alive "$pidf" && grep -q "/$(unit "$1")\$" "/proc/$(cat "$pidf")/cgroup"
}
cpu_max() { cat "/sys/fs/cgroup/user.slice/user-$(id -u).slice/user@$(id -u).service/app.slice/$(unit "$1")/cpu.max" 2>/dev/null \
            || systemctl --user show -p CPUQuotaPerSecUSec --value "$(unit "$1")" 2>/dev/null || echo "?"; }
start_site() {  # start_site <site> [scoped cpu%]: start the supervisor, optionally inside the scope
  local site="$1" pct="${2:-}" port pidf="$RUNTIME/run/supernode-$1.pid"
  port="$(node_port "$site")"
  if [ -n "$pct" ]; then  # "max" = inside the scope without a limit
    local quota=(-p "CPUQuota=${pct}%"); [ "$pct" = max ] && quota=()
    setsid nohup systemd-run --user --scope --quiet --unit "$(unit "$site")" "${quota[@]}" \
        bash "$REPO/deploy/flower-vm/supernode.sh" "$site" "$port" \
        >> "$RUNTIME/logs/supernode-$site.log" 2>&1 < /dev/null 9>&- &
  else
    setsid nohup bash "$REPO/deploy/flower-vm/supernode.sh" "$site" "$port" \
        >> "$RUNTIME/logs/supernode-$site.log" 2>&1 < /dev/null 9>&- &
  fi
  echo $! > "$pidf"
}
swap() {  # swap <site> [cpu%]: restart the site's supervisor under the watchdog lock
  exec 9> "$RUNTIME/watchdog.lock"
  flock -w 120 9 || { echo "watchdog lock busy" >&2; exit 1; }
  stop_site "$1" >/dev/null
  systemctl --user stop "$(unit "$1")" 2>/dev/null || true   # leftover empty scope
  start_site "$@"
  flock -u 9
}

cmd="${1:-}"; site="${2:-}"
case "$cmd" in
  prepare|on|off|release) [ -n "$(node_port "$site")" ] || { echo "unknown site '$site' (sites: $NODES)" >&2; exit 2; } ;;
esac
case "$cmd" in
  prepare)
    if in_scope "$site"; then
      log "$site: already in $(unit "$site") (cpu.max $(cpu_max "$site"))"
    else
      swap "$site" max
      sleep 1
      in_scope "$site" || { log "$site: FAILED to start inside $(unit "$site")"; exit 1; }
      log "$site: restarted inside $(unit "$site") without a CPU limit (pid $(cat "$RUNTIME/run/supernode-$site.pid"))"
    fi
    ;;
  on)
    pct="${3:?cpu% (e.g. 10)}"
    if in_scope "$site"; then
      systemctl --user set-property --runtime "$(unit "$site")" "CPUQuota=${pct}%"
      log "$site: CPUQuota=${pct}% (set in place, no restart)"
    else
      swap "$site" "$pct"
      sleep 1
      in_scope "$site" || { log "$site: FAILED to start inside $(unit "$site")"; exit 1; }
      log "$site: restarted inside $(unit "$site") with CPUQuota=${pct}% (pid $(cat "$RUNTIME/run/supernode-$site.pid"))"
    fi
    ;;
  off)
    if in_scope "$site"; then
      systemctl --user set-property --runtime "$(unit "$site")" "CPUQuota="
      log "$site: CPU limit removed (still in $(unit "$site"); 'release' restarts it normally)"
    else
      log "$site: not throttled"
    fi
    ;;
  release)
    if in_scope "$site"; then
      swap "$site"
      log "$site: restarted outside the straggler scope (pid $(cat "$RUNTIME/run/supernode-$site.pid"))"
    else
      log "$site: not in a straggler scope"
    fi
    ;;
  status)
    for entry in $NODES; do
      s="${entry%%:*}"
      if in_scope "$s"; then echo "$s: in $(unit "$s"), cpu.max $(cpu_max "$s")"; else echo "$s: normal"; fi
    done
    ;;
  *) echo "usage: $0 prepare <site> | on <site> <cpu%> | off <site> | release <site> | status" >&2; exit 2 ;;
esac

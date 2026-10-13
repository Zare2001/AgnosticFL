#!/usr/bin/env bash
# E6b straggler: throttle the CPU of one NVFlare hospital on this VM (EXPERIMENTS.md E6b).
#   straggler.sh on <site> <cpu%>     e.g. straggler.sh on nvf-h1 10  (% of one CPU)
#   straggler.sh off <site>
#   straggler.sh status [<site>]
# on: adopts the RUNNING client process and its job (training) processes into a transient system
# scope flhub-straggler-<site>.scope with CPUQuota=<cpu%> (systemd StartTransientUnit with PIDs).
# No restart, so the site stays in its relay job; job processes started later inherit the scope.
# off: lifts the quota (CPUQuota=); the processes stay in the now unlimited scope until they exit.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/common.sh"
SD=(org.freedesktop.systemd1 /org/freedesktop/systemd1 org.freedesktop.systemd1.Manager)

usage() { sed -n '2,4p' "$0" | sed 's/^# \{0,1\}//' >&2; exit 2; }
unit_of() { echo "flhub-straggler-$1.scope"; }
site_pids() {  # client process + its job processes
  local ws="$NVF_SITES_DIR/$1" pid
  pid=$(cat "$ws/pid.fl" 2>/dev/null || true)
  { [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null && echo "$pid"; } || true
  pgrep -f "nvflare.private.fed.app.client.worker_process.*$ws/" || true
}
show() {
  local unit; unit=$(unit_of "$1")
  if systemctl is-active --quiet "$unit"; then
    echo "$1: $(systemctl show "$unit" -p CPUQuotaPerSecUSec --value | sed 's/^/CPUQuotaPerSecUSec=/') " \
      "pids: $(systemctl show "$unit" -p ControlGroup --value | xargs -I{} cat /sys/fs/cgroup{}/cgroup.procs | tr '\n' ' ')"
  else
    echo "$1: not throttled (no $unit)"
  fi
}

cmd="${1:-}"; shift || true
case "$cmd" in
  on)
    [ $# -eq 2 ] || usage
    site=$1 pct=$2
    [[ "$pct" =~ ^[0-9]+$ && "$pct" -ge 1 && "$pct" -le 400 ]] || { echo "straggler: cpu% must be 1..400" >&2; exit 2; }
    [ -f "$NVF_SITES_DIR/$site/startup/fed_client.json" ] || { echo "straggler: no site $site in $NVF_SITES_DIR" >&2; exit 1; }
    mapfile -t pids < <(site_pids "$site")
    [ ${#pids[@]} -gt 0 ] || { echo "straggler: $site has no running client" >&2; exit 1; }
    unit=$(unit_of "$site")
    if systemctl is-active --quiet "$unit"; then
      sudo busctl call "${SD[@]}" AttachProcessesToUnit ssau "$unit" "" ${#pids[@]} "${pids[@]}"
      sudo systemctl set-property --runtime "$unit" "CPUQuota=${pct}%"
    else
      sudo busctl call "${SD[@]}" StartTransientUnit 'ssa(sv)a(sa(sv))' "$unit" fail 2 \
        PIDs au ${#pids[@]} "${pids[@]}" CPUQuotaPerSecUSec t $((pct * 10000)) 0 >/dev/null
    fi
    echo "straggler: $(date -u +%FT%TZ) $site ON at ${pct}% CPU (pids ${pids[*]})"
    show "$site"
    ;;
  off)
    [ $# -eq 1 ] || usage
    unit=$(unit_of "$1")
    if systemctl is-active --quiet "$unit"; then
      sudo systemctl set-property --runtime "$unit" CPUQuota=
      echo "straggler: $(date -u +%FT%TZ) $1 OFF (quota lifted)"
    else
      echo "straggler: $1 was not throttled"
    fi
    show "$1"
    ;;
  status)
    if [ $# -ge 1 ]; then show "$1"; else
      read -ra all <<<"$NVF_SITES"
      for s in "${all[@]}"; do show "$s"; done
    fi
    ;;
  *) usage ;;
esac

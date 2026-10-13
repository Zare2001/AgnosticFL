#!/usr/bin/env bash
# E4b network shaping on the vantage6 VM (PLAN.md §8): egress toward central's vantage6 ports
# only (server 7601, registry 5050), i.e. the node uploads. Central shapes the downloads.
# Usage: bash deploy/vantage6-vm/netem.sh apply <profile> [--minutes N] | clear | show
#   profiles (EXPERIMENTS.md E4b): wan1g 1gbit/5ms, wan100 100mbit/20ms, wan20 20mbit/40ms
#   --minutes N  planned duration (default 60); the revert timer fires at N + 15 min (max 180)
# One root prio qdisc: band 1:1 = netem leaf, reached only by the u32 filters (dst central IP
# and dst port 7601/5050); all other traffic (SSH, bus 7700, DNS) goes to band 1:2 untouched.
# A systemd timer that deletes the root qdisc is scheduled BEFORE the rule is applied; without
# it the script refuses. Idempotent: apply replaces the rule and the timer, clear removes both.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
. "$REPO/deploy/topology.env"
PORTS="$V6_SERVER_PORT $REGISTRY_PORT"
PEER="$FLHUB_CENTRAL_IP"
TC=/usr/sbin/tc
IFACE="$(ip route get "$PEER" | sed -n 's/.* dev \([^ ]*\).*/\1/p' | head -1)"
[ -n "$IFACE" ] || { echo "no route to $PEER" >&2; exit 1; }
log() { echo "[v6-netem] $*"; }

profile() {
  case "$1" in
    wan1g) echo "1gbit 5ms" ;;
    wan100) echo "100mbit 20ms" ;;
    wan20) echo "20mbit 40ms" ;;
    *) echo "unknown profile $1 (wan1g | wan100 | wan20)" >&2; return 1 ;;
  esac
}

timers() { systemctl list-units --all --plain --no-legend 'flhub-netem-revert-*' 2>/dev/null | awk '{print $1}'; }

stop_timers() {
  for u in $(timers); do sudo systemctl stop "$u" 2>/dev/null || true; done
}

clear_rule() {
  if $TC qdisc show dev "$IFACE" | grep -q '^qdisc prio 1: root'; then
    sudo $TC qdisc del dev "$IFACE" root
  fi
}

show() {
  echo "interface $IFACE (route to $PEER)"
  $TC qdisc show dev "$IFACE"
  $TC filter show dev "$IFACE" 2>/dev/null | grep -E 'match|flowid' || true
  echo "revert timers: $(timers | tr '\n' ' ')"
}

case "${1:-}" in
  apply)
    name="${2:-}"; [ -n "$name" ] || { echo "usage: $0 apply <profile> [--minutes N]" >&2; exit 2; }
    read -r rate delay < <(profile "$name")
    minutes=60
    if [ "${3:-}" = "--minutes" ]; then minutes="${4:?--minutes needs a value}"; fi
    [[ "$minutes" =~ ^[0-9]+$ ]] && [ "$minutes" -ge 1 ] || { echo "--minutes must be a positive integer" >&2; exit 2; }
    revert=$((minutes + 15)); [ "$revert" -le 180 ] || revert=180
    stop_timers
    unit="flhub-netem-revert-$(date +%s)"
    sudo systemd-run --quiet --on-active="${revert}m" --unit "$unit" $TC qdisc del dev "$IFACE" root \
      || { echo "could not schedule the revert timer: refusing to shape" >&2; exit 1; }
    systemctl list-timers --all --no-legend "$unit.timer" | grep -q "$unit" \
      || { echo "revert timer $unit not active: refusing to shape" >&2; exit 1; }
    clear_rule
    sudo $TC qdisc add dev "$IFACE" root handle 1: prio bands 3 priomap 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1
    sudo $TC qdisc add dev "$IFACE" parent 1:1 handle 10: netem rate "$rate" delay "$delay" limit 100000
    for p in $PORTS; do
      sudo $TC filter add dev "$IFACE" parent 1: protocol ip prio 1 u32 \
        match ip dst "$PEER/32" match ip dport "$p" 0xffff flowid 1:1
    done
    log "applied $name ($rate, $delay) on $IFACE: egress to $PEER ports $PORTS; revert timer $unit in ${revert} min"
    show
    ;;
  clear)
    clear_rule
    stop_timers
    log "cleared on $IFACE"
    show
    ;;
  show) show ;;
  *) echo "usage: $0 apply <profile> [--minutes N] | clear | show" >&2; exit 2 ;;
esac

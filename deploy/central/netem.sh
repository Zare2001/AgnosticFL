#!/usr/bin/env bash
# Egress shaping on the central VM for E4b (stage-2 PLAN.md §8). Shapes only framework traffic
# from central to the given peers: source ports 8002 (NVFlare), 7601 (vantage6), 5050 (registry),
# 9092 (Flower); SSH, the agent bus (7700) and everything else stay untouched.
#
#   bash deploy/central/netem.sh apply <profile> <peer-ip>... --minutes N   (N = planned + 15, <= 180)
#   bash deploy/central/netem.sh clear
#   bash deploy/central/netem.sh show
#
# Profiles: wan1g (1 Gbit/s, 5 ms), wan100 (100 Mbit/s, 20 ms), wan20 (20 Mbit/s, 40 ms); none = clear.
# apply first schedules a systemd revert timer (survives an agent crash) and refuses to shape if
# that fails; then it builds ONE root prio qdisc: band 1 (default) unshaped, one band with a netem
# leaf (rate + delay) per peer, selected by u32 filters on peer IP + framework source port.
# Re-running apply replaces the rule (idempotent). Owner escape hatch: sudo tc qdisc del dev ens3 root
set -euo pipefail

PORTS=(8002 7601 5050 9092)
STATE="${FLHUB_RUNTIME:-$HOME/flhub-runtime}/netem-central.state"
TC=/usr/sbin/tc

die() { echo "netem.sh: $*" >&2; exit 1; }
iface_for() { ip -o route get "$1" 2>/dev/null | sed -n 's/.* dev \([^ ]*\).*/\1/p' | head -1; }
default_iface() { ip -o route show default 2>/dev/null | sed -n 's/.* dev \([^ ]*\).*/\1/p' | head -1; }
netem_args() {
  case "$1" in
    wan1g)  echo "delay 5ms rate 1gbit" ;;
    wan100) echo "delay 20ms rate 100mbit" ;;
    wan20)  echo "delay 40ms rate 20mbit" ;;
    *) return 1 ;;
  esac
}

show() {
  local ifc="${1:-$(default_iface)}"
  echo "== tc qdisc show dev $ifc"; $TC qdisc show dev "$ifc"
  echo "== tc filter show dev $ifc parent 1:"; $TC filter show dev "$ifc" parent 1: 2>/dev/null || true
  echo "== revert timers"; systemctl list-timers --all 'flhub-netem-revert-*' --no-pager 2>/dev/null | sed -n '1,8p'
  [ -f "$STATE" ] && { echo "== state"; cat "$STATE"; } || true
}

clear_rules() {
  local ifc="${1:-$(default_iface)}"
  if $TC qdisc show dev "$ifc" | grep -q "qdisc prio 1: root"; then
    sudo $TC qdisc del dev "$ifc" root
    echo "removed root qdisc on $ifc"
  else
    echo "no flhub root qdisc on $ifc"
  fi
  for t in $(systemctl list-units --all --plain --no-legend 'flhub-netem-revert-*' 2>/dev/null | awk '{print $1}'); do
    sudo systemctl stop "$t" 2>/dev/null || true
  done
  rm -f "$STATE"
}

apply() {
  local profile="" minutes="" peers=()
  while [ $# -gt 0 ]; do
    case "$1" in
      --minutes) minutes="$2"; shift 2 ;;
      -*) die "unknown option $1" ;;
      *) if [ -z "$profile" ]; then profile="$1"; else peers+=("$1"); fi; shift ;;
    esac
  done
  [ "$profile" = none ] && { clear_rules; return; }
  local args; args="$(netem_args "$profile")" || die "unknown profile '$profile' (wan1g|wan100|wan20|none)"
  [ ${#peers[@]} -ge 1 ] || die "give the peer IPs (all peers in one call)"
  [ ${#peers[@]} -le 14 ] || die "at most 14 peers"
  [[ "$minutes" =~ ^[0-9]+$ ]] && [ "$minutes" -ge 1 ] && [ "$minutes" -le 180 ] \
    || die "--minutes N is required (planned duration + 15, at most 180)"
  local ifc="" p
  for p in "${peers[@]}"; do
    [[ "$p" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "peer '$p' is not an IPv4 address"
    case "$p" in 127.*|145.38.205.58) die "refusing to shape traffic to $p (local / own address)" ;; esac
    local pi; pi="$(iface_for "$p")"; [ -n "$pi" ] || die "no route to $p"
    [ -z "$ifc" ] || [ "$ifc" = "$pi" ] || die "peers use different interfaces ($ifc, $pi)"
    ifc="$pi"
  done
  # 1. the revert timer first; no timer, no shaping
  local unit="flhub-netem-revert-$(date +%s)"
  sudo systemd-run --on-active="${minutes}m" --unit "$unit" $TC qdisc del dev "$ifc" root \
    || die "could not schedule the revert timer; refusing to shape"
  systemctl list-timers --all "$unit.timer" --no-pager --no-legend | grep -q "$unit" \
    || die "revert timer $unit not visible; refusing to shape"
  # 2. one root qdisc for all peers (replace an existing one)
  if $TC qdisc show dev "$ifc" | grep -q " root "; then sudo $TC qdisc del dev "$ifc" root 2>/dev/null || true; fi
  local bands=$(( ${#peers[@]} + 1 )) band=2
  sudo $TC qdisc add dev "$ifc" root handle 1: prio bands "$bands" priomap 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0
  for p in "${peers[@]}"; do
    # shellcheck disable=SC2086
    sudo $TC qdisc add dev "$ifc" parent "1:$band" handle "$(( band * 10 )):" netem $args limit 100000
    for port in "${PORTS[@]}"; do
      sudo $TC filter add dev "$ifc" parent 1: protocol ip prio 1 u32 \
        match ip dst "$p/32" match ip sport "$port" 0xffff flowid "1:$band"
    done
    band=$(( band + 1 ))
  done
  printf 'profile=%s\npeers=%s\niface=%s\nrevert_unit=%s\nminutes=%s\napplied_utc=%s\n' \
    "$profile" "${peers[*]}" "$ifc" "$unit" "$minutes" "$(date -u +%FT%TZ)" > "$STATE"
  show "$ifc"
}

case "${1:-}" in
  apply) shift; apply "$@" ;;
  clear) clear_rules ;;
  show) show ;;
  *) sed -n '2,16p' "$0"; exit 2 ;;
esac

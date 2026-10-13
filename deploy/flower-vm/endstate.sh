#!/usr/bin/env bash
# Stage-2 end state of the flower VM (PLAN.md §9, DoD 8): prints the evidence for 'S2 report-ready
# flower' and exits 0 only if every item holds.
#   bash deploy/flower-vm/endstate.sh
set -uo pipefail
cd "$(dirname "$0")"
. ./common.sh
ok=0
check() {  # check <label> <0|1> <detail>
  if [ "$2" = 0 ]; then echo "OK    $1: $3"; else echo "FAIL  $1: $3"; ok=1; fi
}
iface="$(ip route get "$FLHUB_CENTRAL_IP" | sed -n 's/.* dev \([^ ]*\).*/\1/p' | head -1)"
qd="$(tc qdisc show dev "$iface" | head -1)"
case "$qd" in *prio*|*netem*|*tbf*|*htb*) r=1 ;; *) r=0 ;; esac
check "tc on $iface" "$r" "$qd"
timers="$(systemctl list-units --all --plain --no-legend 'flhub-netem-revert-*' 2>/dev/null | awk '{print $1}' | tr '\n' ' ')"
check "netem revert timers" "$([ -z "$timers" ] && echo 0 || echo 1)" "${timers:-none}"
scopes="$(systemctl --user list-units --all --plain --no-legend 'flhub-straggler-*' 2>/dev/null | awk '{print $1}' | tr '\n' ' ')"
check "straggler scopes" "$([ -z "$scopes" ] && echo 0 || echo 1)" "${scopes:-none}"
armed="$(for f in "$RUNTIME"/e6/inject-*.pid; do [ -f "$f" ] && pid_alive "$f" && basename "$f"; done | tr '\n' ' ')"
check "armed injections" "$([ -z "$armed" ] && echo 0 || echo 1)" "${armed:-none}"
procs="$(pgrep -af 'run_follow\.py|scripts/sysmon\.py|island|hub\.core|adapters\.flower|flower-superlink' | grep -v pgrep | cut -c1-80 | tr '\n' ';')"
check "experiment / island processes" "$([ -z "$procs" ] && echo 0 || echo 1)" "${procs:-none}"
extras="$(FLHUB_FLOWER_RUNTIME="$HOME/flhub-runtime/flower-e5" FLHUB_FLOWER_NODES="flw-h11:9096 flw-h12:9097" \
          bash -c '. ./common.sh; for e in $NODES; do pid_alive "$RUNTIME/run/supernode-${e%%:*}.pid" && echo "${e%%:*}"; done' | tr '\n' ' ')"
check "E5 extra SuperNodes" "$([ -z "$extras" ] && echo 0 || echo 1)" "${extras:-stopped}"
base="$(bash ./status.sh 2>&1)"; echo "$base" | sed 's/^/      /'
check "base hospitals flw-h5 flw-h6" "$(echo "$base" | tail -1 | grep -q ': HEALTHY$' && echo 0 || echo 1)" "$(echo "$base" | tail -1)"
free_gb="$(df -BG --output=avail / | tail -1 | tr -dc 0-9)"
check "free disk" "$([ "$free_gb" -ge 3 ] && echo 0 || echo 1)" "${free_gb} GB free on /"
[ "$ok" = 0 ] && echo "flower-vm end state: CLEAN" || echo "flower-vm end state: NOT CLEAN"
exit "$ok"

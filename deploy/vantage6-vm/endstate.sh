#!/usr/bin/env bash
# S5 end-state evidence on the vantage6 VM (PLAN.md §9 item 8, VANTAGE6.md S5): prints each check
# and exits 0 only if all hold. No changes are made.
#   bash deploy/vantage6-vm/endstate.sh
set -uo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
IFACE="$(ip route get 145.38.205.58 | sed -n 's/.* dev \([^ ]*\).*/\1/p' | head -1)"
fail=0
check() {  # check <label> <0|1> <detail>
  printf '%-34s %-4s %s\n' "$1" "$([ "$2" = 0 ] && echo OK || echo FAIL)" "$3"
  [ "$2" = 0 ] || fail=1
}
q="$(tc qdisc show dev "$IFACE" | head -1)"
check "tc qdisc ($IFACE)" "$(echo "$q" | grep -q '^qdisc fq_codel 0: root' && echo 0 || echo 1)" "$q"
t="$(systemctl list-units --all --plain --no-legend 'flhub-netem-revert-*' | wc -l)"
check "flhub-netem revert timers" "$([ "$t" = 0 ] && echo 0 || echo 1)" "$t units"
i="$(bash "$REPO/deploy/vantage6-vm/inject.sh" status | tr '\n' ' ')"
check "inject.sh" "$(echo "$i" | grep -q 'no injection armed' && echo 0 || echo 1)" "$i"
s="$(bash "$REPO/deploy/vantage6-vm/straggler.sh" status | tr '\n' ' ')"
check "straggler.sh" "$(echo "$s" | grep -q 'no straggler on' && echo 0 || echo 1)" "$s"
lim="$(for c in $(docker ps -q --filter label=vantage6-type=algorithm); do docker inspect --format '{{.Name}} {{.HostConfig.NanoCpus}}' "$c"; done | awk '$2 != 0' | wc -l)"
check "throttled algorithm containers" "$([ "$lim" = 0 ] && echo 0 || echo 1)" "$lim"
x="$(docker ps --format '{{.Names}}' | grep -cE '^vantage6-v6-h(9|10)-user$')"
check "E5 extra nodes v6-h9/v6-h10" "$([ "$x" = 0 ] && echo 0 || echo 1)" "$x running"
c="$(crontab -l 2>/dev/null | grep -c 'deploy/vantage6-vm/extra.sh start')"
check "E5 watchdog cron entry" "$([ "$c" = 0 ] && echo 0 || echo 1)" "$c entries"
o="$(docker ps --format '{{.Names}}' | grep -cE '^vantage6-(isl-|flhub-operator)')"
check "island / native nodes" "$([ "$o" = 0 ] && echo 0 || echo 1)" "$o running"
imgs="$(docker images --format '{{.Repository}}:{{.Tag}}' | sort | tr '\n' ' ')"
n="$(docker images --format '{{.Repository}}' | grep -c 'flhub/v6-train')"
check "v6-train images (current+prev)" "$([ "$n" -le 2 ] && echo 0 || echo 1)" "$imgs"
st="$(bash "$REPO/deploy/vantage6-vm/status.sh" 2>&1)"; rc=$?
check "base nodes v6-h3/v6-h4" "$rc" "$(echo "$st" | grep -E '^v6-h' | awk '{print $1"="$2}' | tr '\n' ' ')"
w="$(crontab -l 2>/dev/null | grep -c 'deploy/vantage6-vm/start.sh')"
check "base watchdog cron entry" "$([ "$w" = 1 ] && echo 0 || echo 1)" "$w entry"
free_gb="$(df -BG --output=avail / | tail -1 | tr -dc 0-9)"
check "disk free >= 3 GB" "$([ "$free_gb" -ge 3 ] && echo 0 || echo 1)" "$(df -h / | awk 'NR == 2 {print $4 " free of " $2}')"
# anchored on the processes' own command lines, so a shell that merely mentions them does not count
p="$(pgrep -fc '^(/usr/bin/)?python3 \S*scripts/sysmon\.py run|^python3 \S*run_follow\.py|^bash \S*(inject|straggler)\.sh _' || true)"
check "experiment processes" "$([ "$p" = 0 ] && echo 0 || echo 1)" "$p (sysmon, follower, injectors)"
echo "end state: $([ "$fail" = 0 ] && echo CLEAN || echo NOT CLEAN)"
exit $fail

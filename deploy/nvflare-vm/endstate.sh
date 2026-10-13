#!/usr/bin/env bash
# End-state evidence for the stage-2 Definition of Done (PLAN.md §9 item 8) on the NVFlare VM.
# Prints each check with its command output; exit 0 only when all pass.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/common.sh"
IFACE="$(ip route get "$FLHUB_CENTRAL_IP" | sed -n 's/.* dev \([^ ]*\).*/\1/p' | head -1)"
ok=1
check() { if [ "$1" = 0 ]; then echo "PASS $2"; else echo "FAIL $2"; ok=0; fi; }

echo "== $(date -u +%FT%TZ) nvflare VM end state"
q=$(tc qdisc show dev "$IFACE"); echo "$q" | sed 's/^/   /'
echo "$q" | grep -qE 'netem|prio 1: root'; check $((! $?)) "no tc shaping rules on $IFACE"
t=$(systemctl list-units --all --plain --no-legend 'flhub-netem-revert-*' 2>/dev/null)
[ -z "$t" ]; check $? "no flhub-netem revert timers ($(echo "$t" | grep -c . ) units)"
a=$(bash "$HERE/inject.sh" status 2>&1 | head -1); echo "   $a"
[ "$a" = "inject: nothing armed" ]; check $? "no armed injection"
s=$(systemctl list-units --all --plain --no-legend 'flhub-straggler-*' 2>/dev/null | awk '{print $1}')
thr=0; for u in $s; do q2=$(systemctl show "$u" -p CPUQuotaPerSecUSec --value); echo "   $u CPUQuota=$q2"; [ "$q2" = infinity ] || thr=1; done
check $thr "no CPU-throttled site (straggler scopes unlimited or absent)"
w=$(pgrep -fc "inject_watch\.py" || true); [ "${w:-0}" = 0 ]; check $? "no inject watcher processes"
for x in $NVF_EXTRA_SITES; do
  if nvf_pid_alive "$NVF_SITES_DIR/$x/daemon_pid.fl" || [ -e "$NVF_SITES_DIR/$x/.enabled" ]; then check 1 "extra site $x stopped"; else check 0 "extra site $x stopped"; fi
done
h=$(bash "$HERE/status.sh" 2>&1); echo "$h" | sed 's/^/   /'
[ "$(echo "$h" | tail -1)" = "status: HEALTHY" ]; check $? "base hospitals connected (status.sh HEALTHY)"
free_gb=$(df -BG --output=avail / | tail -1 | tr -dc 0-9); echo "   / free ${free_gb} GB"
[ "$free_gb" -ge 3 ]; check $? "disk >= 3 GB free"
echo "== $([ $ok = 1 ] && echo ALL PASS || echo SOME FAILED)"
[ $ok = 1 ]

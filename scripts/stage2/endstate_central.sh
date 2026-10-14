#!/usr/bin/env bash
# End-state evidence on central for the stage-2 DoD (PLAN.md stage 2 §9.8). Read-only checks:
# no tc rules, no netem revert timers, no experiment processes, E5 extra sites offline, >= 3 GB free,
# hub core up and the six base sites online in all three adapters.
#   bash scripts/stage2/endstate_central.sh        (exit 0 only if every check passes)
set -uo pipefail
cd "$(dirname "$0")/../.."
fail=0
check() {  # check <label> <ok 0|1> <detail>
  if [ "$2" -eq 0 ]; then echo "OK    $1: $3"; else echo "FAIL  $1: $3"; fail=1; fi
}
utc() { date -u +%FT%TZ; }
echo "central end state $(utc) at $(git rev-parse --short HEAD)"

iface="$(ip -o route get 145.38.189.142 | sed -n 's/.* dev \([^ ]*\).*/\1/p')"
q="$(/usr/sbin/tc qdisc show dev "$iface" | head -1)"
echo "$q" | grep -qE "^qdisc (fq_codel|mq|pfifo_fast|noqueue) 0: root"; check "tc $iface" $? "$q"
t="$(systemctl list-timers --all 'flhub-netem-revert-*' --no-pager --no-legend 2>/dev/null | grep -c flhub-netem-revert)"
[ "$t" -eq 0 ]; check "revert timers" $? "$t flhub-netem-revert timers"

procs="$(ps -eo pid,args | grep -E 'run_final\.py|postproc\.py|hub\.sim|inject_central\.py|e4a_sweep\.py|e4b_run\.py|e5_run\.py|campaign_e[0-9]|sysmon\.py run' | grep -v grep | cut -c1-110)"
[ -z "$procs" ]; check "experiment processes" $? "${procs:-none}"
[ ! -e "$HOME/flhub-runtime/runs/stage2/ACTIVE_RUN" ] && [ ! -e "$HOME/flhub-runtime/runs/stage2/HOLD_POST" ] \
  && [ ! -e "$HOME/flhub-runtime/runs/stage2/PAUSE" ]; check "run markers" $? "no ACTIVE_RUN / HOLD_POST / PAUSE"
n="$(ls "$HOME/flhub-runtime/runs/stage2/queue" 2>/dev/null | wc -l)"
[ "$n" -eq 0 ]; check "post-processing queue" $? "$n jobs"

free_gb="$(df -BG --output=avail / | tail -1 | tr -dc 0-9)"
[ "$free_gb" -ge 3 ]; check "disk" $? "$(df -h --output=avail,used,size / | tail -1 | xargs) (avail/used/size)"

curl -fsS -m 5 http://127.0.0.1:8800/health >/dev/null; check "hub core" $? ":8800 /health"
for fw in nvflare vantage6 flower; do
  out="$(bash deploy/central/$fw/status.sh 2>&1)"
  echo "$out" | grep -qE "HEALTHY|^adapter +OK" && ! echo "$out" | grep -q "NOT HEALTHY"
  check "$fw status" $? "$(echo "$out" | grep -E 'HEALTHY|adapter +OK|discover' | head -2 | tr -s ' ' | tr '\n' ';')"
done
for port in 8811 8812 8813; do
  d="$(curl -fsS -m 10 -X POST -H 'Content-Type: application/json' "http://127.0.0.1:$port/rpc" \
    -d '{"jsonrpc":"2.0","id":1,"method":"discover","params":{"protocol":"flhub/0.2"}}' 2>/dev/null)"
  sites="$(echo "$d" | python3 -c 'import json,sys; r=json.load(sys.stdin)["result"]; print(" ".join(f"{s["site_id"]}={s["status"]}" for s in r["sites"]))' 2>/dev/null)"
  base_ok=1
  echo "$sites" | grep -qE "(nvf-h1|v6-h3|flw-h5)=online" && echo "$sites" | grep -qE "(nvf-h2|v6-h4|flw-h6)=online" && base_ok=0
  extra_on="$(echo "$sites" | tr ' ' '\n' | grep -E '^(nvf-h7|nvf-h8|v6-h9|v6-h10|flw-h11|flw-h12)=online' || true)"
  [ $base_ok -eq 0 ] && [ -z "$extra_on" ]; check "adapter :$port sites" $? "${sites:-no answer}"
done
echo "result: $([ $fail -eq 0 ] && echo PASS || echo FAIL)"
exit $fail

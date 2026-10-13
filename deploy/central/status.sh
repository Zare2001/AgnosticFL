#!/usr/bin/env bash
# Health of everything on the central VM: bus, hub core, registry, and each framework's status.sh.
# Exit code 0 only if all are healthy.
. "$(dirname "$0")/common.sh"
fail=0
echo "central VM status ($(date -u +%FT%TZ))"
if curl -sf "http://127.0.0.1:$AGENTBUS_PORT/health" >/dev/null; then ok "agent bus :$AGENTBUS_PORT"; else bad "agent bus :$AGENTBUS_PORT"; fail=1; fi
if h=$(curl -sf "http://127.0.0.1:$HUB_CORE_PORT/health"); then ok "hub core :$HUB_CORE_PORT" "$h"; else bad "hub core :$HUB_CORE_PORT"; fail=1; fi
if curl -sf "http://127.0.0.1:$REGISTRY_PORT/v2/" >/dev/null; then
  ok "registry :$REGISTRY_PORT" "$(curl -s "http://127.0.0.1:$REGISTRY_PORT/v2/_catalog")"
else bad "registry :$REGISTRY_PORT"; fail=1; fi
for fw in nvflare vantage6 flower; do
  s="$REPO/deploy/central/$fw/status.sh"
  if [ -x "$s" ] || [ -f "$s" ]; then
    if out=$(bash "$s" 2>&1); then ok "$fw (deploy/central/$fw)"; else bad "$fw (deploy/central/$fw)"; fail=1; fi
    echo "$out" | sed 's/^/      /'
  else
    printf '  %-28s --   not deployed yet\n' "$fw"
  fi
done
[ "$fail" = 0 ] && echo "HEALTHY" || echo "UNHEALTHY"
exit "$fail"

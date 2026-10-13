#!/usr/bin/env bash
# Health of the Flower server side: SuperLink ports, adapter, relay run and hospital sites.
# Exit 0 only when SuperLink, adapter and relay are up and the base Flower sites are online
# (flw-h5, flw-h6; the E5 extras flw-h11/flw-h12 are registered but normally offline).
#   --no-sites   do not require the sites (e.g. right after deploy, before hospitals connect)
set -uo pipefail
. "$(dirname "$0")/common.sh"
need_sites=1
[ "${1:-}" = "--no-sites" ] && need_sites=0

ok=0
check() {  # check <label> <command...>
  local label="$1"; shift
  if "$@"; then echo "OK    $label"; else echo "FAIL  $label"; ok=1; fi
}
check "superlink process" pid_alive "$RUNTIME/run/superlink.pid"
check "superlink fleet API ${FLEET_BIND}" port_open "${FLEET_BIND##*:}"
check "superlink HTTP API 127.0.0.1:$CONTROL_PORT" port_open "$CONTROL_PORT"
check "adapter process" pid_alive "$RUNTIME/run/adapter.pid"
check "adapter /health on :$ADAPTER_PORT" curl -sf "http://127.0.0.1:$ADAPTER_PORT/health" -o /dev/null

"$VENV/bin/python" - "$ADAPTER_PORT" "$need_sites" $BASE_SITES_FLOWER <<'PY' || ok=1
import json, sys, urllib.request
base = f"http://127.0.0.1:{sys.argv[1]}"
try:
    relay = json.load(urllib.request.urlopen(base + "/relay/status", timeout=10))
    req = urllib.request.Request(base + "/rpc", json.dumps({"jsonrpc": "2.0", "id": 1, "method": "discover",
                                 "params": {"protocol": "flhub/0.1"}}).encode(), {"Content-Type": "application/json"})
    disc = json.load(urllib.request.urlopen(req, timeout=10))["result"]
except Exception as exc:
    print(f"FAIL  adapter relay status / discover: {exc}")
    sys.exit(1)
seen = relay["relay_last_poll_s"]
alive = relay.get("relay_alive", seen is not None and seen < 45)
run = (relay.get("relay") or {}).get("run_id")
print(("OK    " if alive else "FAIL  ") + f"relay run {run} alive (last poll {seen} s ago, pending {relay['pending']}, "
      f"fab {relay.get('fab_id')}, flhub_core {(relay.get('relay') or {}).get('code_version')})")
sites = {s["site_id"]: s["status"] for s in disc["sites"]}
print(f"      discover: {disc['adapter']} flwr {disc['framework_version']}: "
      + ", ".join(f"{k}={v}" for k, v in sites.items()))
base_sites = sys.argv[3:]
ready = all(sites.get(s) == "online" for s in base_sites)
print(f"READY: base Flower sites {' '.join(base_sites)} online" if ready else "NOT READY: waiting for hospital SuperNodes")
sys.exit(0 if alive and (ready or sys.argv[2] == "0") else 1)
PY
[ "$ok" = 0 ] && echo "flower central: HEALTHY" || echo "flower central: UNHEALTHY"
exit "$ok"

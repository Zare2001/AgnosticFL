#!/usr/bin/env bash
# Health of the NVFlare side on the central VM: server, adapter, relay job, hospital sites.
# Exit 0 only when everything is up and every base NVFlare site is online (extra sites may be offline).
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/common.sh"
ok=1

if nvf_pid_alive "$NVF_SERVER_KIT/daemon_pid.fl" && nvf_port_open "$NVF_FL_PORT" && nvf_port_open "$NVF_ADMIN_PORT"; then
  echo "server : up ($NVF_SERVER_NAME, fed :$NVF_FL_PORT, admin :$NVF_ADMIN_PORT)"
else
  echo "server : DOWN"; ok=0
fi

if nvf_pid_alive "$NVF_ADAPTER_PID" && nvf_port_open "$NVF_ADAPTER_PORT"; then
  echo "adapter: up (pid $(cat "$NVF_ADAPTER_PID"), 127.0.0.1:$NVF_ADAPTER_PORT)"
  "$NVF_VENV/bin/python" - "$NVF_ADAPTER_PORT" "$NVF_BASE_SITES" <<'PY' || ok=0
import json, sys, urllib.request
port, base = sys.argv[1], sys.argv[2].split()
def rpc(method):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": {"protocol": "flhub/0.1"}}).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{port}/rpc", data=body, headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=15))
d = rpc("discover").get("result") or {}
sites = d.get("sites", [])
print(f"nvflare: {d.get('framework_version')}  protocol {d.get('protocol')}")
for s in sites:
    print(f"site   : {s['site_id']:8s} {s['status']}{'' if s['site_id'] in base else '  (extra)'}")
try:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/relay/status", timeout=10) as r:
        print("relay  :", json.dumps(json.load(r)))
except Exception as e:
    print("relay  : status unavailable:", e)
status = {s["site_id"]: s["status"] for s in sites}
sys.exit(0 if base and all(status.get(b) == "online" for b in base) else 1)
PY
else
  echo "adapter: DOWN"; ok=0
fi

[ "$ok" = 1 ] && echo "status : HEALTHY" || echo "status : NOT HEALTHY"
[ "$ok" = 1 ]

#!/usr/bin/env bash
# Health of the vantage6 side on the central VM. Exit 0 = healthy: server, algorithm image and
# adapter up and both base sites online in the adapter's discover (E5 extras may be offline).
# --no-sites: skip the site check (e.g. before the hospitals have switched over); --strict is
# accepted (the default).
set -uo pipefail
REPO="$(cd "$(dirname "$0")/../../.." && pwd)"
. "$REPO/deploy/topology.env"
NAME=flhub
RT="${V6_RT:-$HOME/flhub-runtime/vantage6}"
SERVER_URL="$(grep '^V6_SERVER_URL=' "$RT/adapter.env" 2>/dev/null | cut -d= -f2- || true)"
SERVER_URL="${SERVER_URL:-http://127.0.0.1}"
BASE_SITES="v6-h3 v6-h4"   # health covers the base sites; E5 extras may be offline
ok=0
if [ -n "$(docker ps -q --filter "label=vantage6-type=server" --filter "label=name=$NAME")" ] \
   && v="$(curl -sf "$SERVER_URL:$V6_SERVER_PORT/api/version")"; then
  echo "server   OK    $v on :$V6_SERVER_PORT"
else
  echo "server   DOWN  (container or :$V6_SERVER_PORT/api/version)"; ok=1
fi
img="$(grep '^V6_ALGO_IMAGE=' "$RT/adapter.env" 2>/dev/null | cut -d= -f2-)"
repo_tag="${img#*/}"
if [ -n "$img" ] && curl -sf "http://127.0.0.1:$REGISTRY_PORT/v2/${repo_tag%:*}/tags/list" \
     | grep -q "\"${repo_tag##*:}\""; then
  echo "image    OK    $img"
else
  echo "image    MISSING ${img:-<no adapter.env>} in registry :$REGISTRY_PORT"; ok=1
fi
d="$(curl -sf -X POST "http://127.0.0.1:$ADAPTER_V6_PORT/rpc" \
     -d '{"jsonrpc":"2.0","id":1,"method":"discover","params":{"protocol":"flhub/0.1"}}')"
if [ -n "$d" ]; then
  echo "adapter  OK    :$ADAPTER_V6_PORT $(echo "$d" | python3 -c 'import json,sys; r=json.load(sys.stdin)["result"]; print(r["framework"], r["framework_version"], " ".join(s["site_id"] + "=" + s["status"] for s in r["sites"]))')"
  if [ "${1:-}" != "--no-sites" ] && ! echo "$d" | python3 -c 'import json, sys
st = {s["site_id"]: s["status"] for s in json.load(sys.stdin)["result"]["sites"]}
sys.exit(0 if all(st.get(b) == "online" for b in sys.argv[1:]) else 1)' $BASE_SITES; then
    echo "sites    BASE SITES NOT ALL ONLINE ($BASE_SITES)"; ok=1
  fi
else
  echo "adapter  DOWN  :$ADAPTER_V6_PORT"; ok=1
fi
exit $ok

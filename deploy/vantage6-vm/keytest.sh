#!/usr/bin/env bash
# Side-by-side test of hospital-made node keys (vantage6 VM), next to the live nodes v6-h3/v6-h4.
# Test collaboration flhub-keytest: organisations v6-h3 and v6-h4 each get a SECOND node there
# (vantage6: one node per organisation and collaboration), run here as v6-h3k / v6-h4k with the
# same benchmark data, so the conformance suite applies. The live adapter (collaboration flhub)
# does not see these nodes; the live node configs, keys and the watchdog are not touched.
#   keys             make_key.sh v6-h3/v6-h4 --collab flhub-keytest (sent encrypted to central)
#   install          render the node configs v6-h3k / v6-h4k from those keys
#   start | stop     start (and wait until connected) / stop the test nodes
#   status           connection state of the test nodes
#   check            node logins: registered keys 200, a fresh unregistered UUID4 401,
#                    a rotated-away key (keys/*.key.old) 401
#   badnode          start a node v6-h3x with an unregistered key: it must not connect; then remove it
#   rotate SITE      make_key.sh SITE --rotate --collab flhub-keytest (central registers it; then
#                    run: check, install, stop, start)
#   clean            stop the test nodes; delete their configs, task dirs, logs and test keys
# Central side (register, adapter on :8822, conformance, clean-up): deploy/central/vantage6/keytest.sh
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
. "$REPO/deploy/topology.env"
. "$REPO/deploy/versions.env"
. "$REPO/deploy/vantage6-vm/common.sh"
COLLAB=flhub-keytest
SITES="v6-h3 v6-h4"
VENV="$HOME/venvs/v6"
KEYS_DIR="$HOME/flhub-runtime/vantage6/keys"
API="http://$FLHUB_CENTRAL_IP:$V6_SERVER_PORT/api"
log() { echo "[v6-keytest] $*"; }
node_of() { echo "${1}k"; }
keyfile() { echo "$KEYS_DIR/$1@$COLLAB.key"; }
node_login() {  # HTTP status of a node login; the key comes on stdin (never in argv)
  python3 -c 'import json,sys; print(json.dumps({"api_key": sys.stdin.read().strip()}))' \
    | curl -s -o /dev/null -w '%{http_code}' -H 'Content-Type: application/json' -d @- "$API/token/node"
}
expect() {  # expect LABEL CODE < key
  local got; got="$(node_login)"
  if [ "$got" = "$2" ]; then log "PASS $1: HTTP $got"; else log "FAIL $1: HTTP $got, expected $2"; return 1; fi
}
allowed() { echo "^$(echo "$FLHUB_CENTRAL_IP:$REGISTRY_PORT" | sed 's/\./\\./g')/flhub/v6-train(:[A-Za-z0-9._-]+)?(@sha256:[a-f0-9]{64})?\$"; }
start_node() {
  if [ -z "$(docker ps -q --filter "name=^vantage6-$1-user$")" ]; then
    (cd "$HOME" && "$VENV/bin/v6" node start -n "$1" --user >/dev/null); log "started node $1"
  fi
}
stop_node() {
  local ids; ids="$(docker ps -q --filter "name=^vantage6-$1-user$")"
  if [ -n "$ids" ]; then docker stop $ids >/dev/null; log "stopped node $1"; fi
}

case "${1:-}" in
  keys)
    for s in $SITES; do bash "$REPO/deploy/vantage6-vm/make_key.sh" "$s" --collab "$COLLAB"; done ;;
  install)
    for s in $SITES; do
      f="$(keyfile "$s")"; [ -f "$f" ] || { echo "no $f: run $0 keys" >&2; exit 1; }
      render_node_config "$(node_of "$s")" "$s" "$(tr -d '[:space:]' < "$f")" "http://$FLHUB_CENTRAL_IP" "$(allowed)"
      log "node config ~/.config/vantage6/node/$(node_of "$s").yaml (data of $s, key fp=$(tr -d '[:space:]' < "$f" | sha256sum | cut -c1-12))"
    done ;;
  start)
    for s in $SITES; do start_node "$(node_of "$s")"; done
    for s in $SITES; do wait_connected "$(node_of "$s")"; log "node $(node_of "$s") connected"; done ;;
  stop)
    for s in $SITES; do stop_node "$(node_of "$s")"; done ;;
  status)
    ok=0
    for s in $SITES; do
      n="$(node_of "$s")"
      if [ -z "$(docker ps -q --filter "name=^vantage6-$n-user$")" ]; then echo "$n  DOWN"; ok=1; continue; fi
      last="$(docker logs "vantage6-$n-user" 2>&1 | grep -E 'Connected to the /tasks|Disconnected from the server' | tail -1)"
      case "$last" in *Connected*) echo "$n  OK    connected" ;; *) echo "$n  DISCONNECTED"; ok=1 ;; esac
    done
    exit $ok ;;
  check)
    rc=0
    for s in $SITES; do
      f="$(keyfile "$s")"; expect "$s@$COLLAB registered key" 200 < "$f" || rc=1
      if [ -f "$f.old" ]; then expect "$s@$COLLAB rotated-away key" 401 < "$f.old" || rc=1; fi
    done
    python3 -c 'import uuid; print(uuid.uuid4())' | expect "fresh unregistered UUID4" 401 || rc=1
    exit $rc ;;
  badnode)
    n=v6-h3x
    render_node_config "$n" v6-h3 "$(python3 -c 'import uuid; print(uuid.uuid4())')" "http://$FLHUB_CENTRAL_IP" "$(allowed)"
    (cd "$HOME" && "$VENV/bin/v6" node start -n "$n" --user >/dev/null) || true
    # the node container is auto-removed when it exits: follow its log while it runs (max 60 s)
    nlog="$(mktemp)"
    timeout 60 docker logs -f "vantage6-$n-user" > "$nlog" 2>&1 || true
    if grep -qE 'Connected to the /tasks|Websocket connection established' "$nlog"; then
      log "FAIL $n with an unregistered key connected"; rc=1
    elif [ ! -s "$nlog" ]; then
      log "FAIL $n: no node log captured (container did not start?)"; rc=1
    else
      log "PASS $n with an unregistered key did not connect ($(wc -l < "$nlog") log lines); the lines that matter:"
      sed 's/\x1b\[[0-9;]*m//g' "$nlog" | grep -iE 'authenticat|401|unauthor|invalid|exit' | grep -v -i 'api_key:' | tail -5 | sed 's/^/    /'
      rc=0
    fi
    rm -f "$nlog"
    stop_node "$n"; docker rm -f "vantage6-$n-user" >/dev/null 2>&1 || true
    rm -rf "$HOME/.config/vantage6/node/$n.yaml" "$HOME/.local/share/vantage6/node/$n"
    exit $rc ;;
  rotate)
    s="${2:?site}"; bash "$REPO/deploy/vantage6-vm/make_key.sh" "$s" --rotate --collab "$COLLAB" ;;
  clean)
    for s in $SITES; do
      n="$(node_of "$s")"; stop_node "$n"; docker rm -f "vantage6-$n-user" >/dev/null 2>&1 || true
      rm -rf "$HOME/.config/vantage6/node/$n.yaml" "$HOME/.local/share/vantage6/node/$n"
      f="$(keyfile "$s")"; for k in "$f" "$f.old" "$f.registered"; do [ -f "$k" ] && shred -u "$k"; done
    done
    log "test nodes, configs, task dirs and test keys removed (central: deploy/central/vantage6/keytest.sh clean)" ;;
  *) echo "usage: $0 keys|install|start|stop|status|check|badnode|rotate SITE|clean" >&2; exit 2 ;;
esac

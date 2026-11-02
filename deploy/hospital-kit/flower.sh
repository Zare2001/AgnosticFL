# shellcheck shell=bash
# Flower hospital (sourced as framework.sh): the standard flower-supernode, connecting out to the
# operator's SuperLink. Nothing of the hub is installed: the ClientApp and its training code
# (flhub_core) arrive in the FAB of the operator's run. Hospital settings are only the node config:
# site-id and data-path.
SUPERLINK="$CENTRAL_IP:$FLOWER_FLEET_PORT"

descendants() { local c; for c in $(pgrep -P "$1"); do echo "$c"; descendants "$c"; done; }

supernode_pid() {  # the flower-supernode process under its restart loop
  pid_alive "$RUN/supernode.pid" && pgrep -P "$(cat "$RUN/supernode.pid")" -f flower-supernode | head -1
}

fw_install() {
  make_venv
  pip_torch
  uv pip install -q --python "$VENV/bin/python" "flwr==$FLWR_VERSION" "numpy==$NUMPY_VERSION" \
    "safetensors==$SAFETENSORS_VERSION"
  log "Flower $FLWR_VERSION SuperNode ready: SuperLink $SUPERLINK, local port $FLOWER_LOCAL_PORT"
}

fw_start() {
  [ -x "$VENV/bin/flower-supernode" ] || die "run ./install.sh first"
  if pid_alive "$RUN/supernode.pid"; then log "already running (pid $(cat "$RUN/supernode.pid"))"; return 0; fi
  tcp_open "$CENTRAL_IP" "$FLOWER_FLEET_PORT" || die "cannot reach the SuperLink $SUPERLINK (firewall?)"
  rm -f "$RUN/supernode.pid"   # the loop writes its own pid (setsid may fork)
  setsid nohup bash "$KIT/flower_supernode.sh" >> "$RUN/supernode.log" 2>&1 < /dev/null &
  log "starting (data $DATA_DIR/<variant>-$SITE.csv, log $RUN/supernode.log)"
  for _ in $(seq 1 60); do
    if fw_status >/dev/null 2>&1; then log "connected to the SuperLink $SUPERLINK"; return 0; fi
    sleep 1
  done
  die "not connected after 60 s; see $RUN/supernode.log"
}

fw_stop() {
  if ! pid_alive "$RUN/supernode.pid"; then log "not running"; rm -f "$RUN/supernode.pid"; return 0; fi
  local pid kids
  pid=$(cat "$RUN/supernode.pid")
  kids=$(descendants "$pid")
  kill "$pid"
  for _ in $(seq 20); do kill -0 "$pid" 2>/dev/null || break; sleep 0.5; done
  kill -0 "$pid" 2>/dev/null && kill -9 "$pid"
  for k in $kids; do kill "$k" 2>/dev/null || true; done
  rm -f "$RUN/supernode.pid"
  log "stopped"
}

fw_status() {
  local pid node
  if ! pid_alive "$RUN/supernode.pid"; then echo "$SITE: DOWN (not running)"; return 1; fi
  pid=$(supernode_pid)
  [ -n "$pid" ] || { echo "$SITE: DOWN (restarting the SuperNode?)"; return 1; }
  node=$(grep -ao 'SuperNode ID: [0-9]*' "$RUN/supernode.log" | tail -1)
  if ! command -v ss >/dev/null; then
    echo "$SITE: up, flower-supernode pid $pid, ${node:-no node id yet} (connection not checked: no ss)"
    [ -n "$node" ]; return
  fi
  if ss -tnp state established 2>/dev/null | grep "pid=$pid," | grep -q "$CENTRAL_IP\]*:$FLOWER_FLEET_PORT"; then
    echo "$SITE: up, flower-supernode pid $pid connected to $SUPERLINK, ${node:-no node id yet}"
  else
    echo "$SITE: NOT CONNECTED (pid $pid, SuperLink $SUPERLINK)"; return 1
  fi
}

# shellcheck shell=bash
# Flower hospital (sourced as framework.sh): the standard flower-supernode, connecting out to the
# operator's SuperLink. Nothing of the hub is installed: the ClientApp and its training code
# (flhub_core) arrive in the FAB of the operator's run. Hospital settings are only the node config:
# site-id and data-path.
# FLOWER_TLS=1 (kit.env): TLS to the SuperLink, verified with the operator's Flower CA
# (credentials/ca.crt, sha256 FLOWER_CA_SHA256 in kit.env), and SuperNode authentication with this
# hospital's own P-384 key credentials/node_key, made here by install.sh. Only node_key.pub and its
# sha256 go to the operator, who registers it; the private key never leaves this machine.
SUPERLINK="$CENTRAL_IP:$FLOWER_FLEET_PORT"
FLOWER_TLS="${FLOWER_TLS:-0}"
NODE_KEY="$KIT/credentials/node_key"
FLOWER_CA="$KIT/credentials/ca.crt"

flower_check_ca() {
  [ -f "$FLOWER_CA" ] || die "no $FLOWER_CA in this kit (ask the operator for a new kit)"
  local got; got=$(sha256sum "$FLOWER_CA" | cut -d' ' -f1)
  [ "$got" = "${FLOWER_CA_SHA256:-}" ] || die "credentials/ca.crt sha256 $got differs from kit.env FLOWER_CA_SHA256"
}

flower_keygen() {  # once: this hospital's SuperNode key pair; prints what to send to the operator
  mkdir -p "$KIT/credentials" && chmod 700 "$KIT/credentials"
  if [ ! -f "$NODE_KEY" ]; then
    (umask 077; ssh-keygen -q -t ecdsa -b 384 -N "" -C "flhub-$SITE" -f "$NODE_KEY")
    log "made this hospital's SuperNode key pair: $NODE_KEY (private, stays here)"
  fi
  chmod 600 "$NODE_KEY"; chmod 644 "$NODE_KEY.pub"
  log "send the operator $NODE_KEY.pub with its sha256 $(sha256sum "$NODE_KEY.pub" | cut -d' ' -f1);"
  log "the SuperNode connects once the operator has registered it"
}

descendants() { local c; for c in $(pgrep -P "$1"); do echo "$c"; descendants "$c"; done; }

supernode_pid() {  # the flower-supernode process under its restart loop
  pid_alive "$RUN/supernode.pid" && pgrep -P "$(cat "$RUN/supernode.pid")" -f flower-supernode | head -1
}

last_start_log() {  # supernode.log since the last start of flower-supernode
  local n
  n=$(grep -an "Starting Flower SuperNode" "$RUN/supernode.log" 2>/dev/null | tail -1 | cut -d: -f1)
  [ -n "$n" ] && tail -n "+$n" "$RUN/supernode.log"
  return 0
}

fw_install() {
  make_venv
  pip_torch
  uv pip install -q --python "$VENV/bin/python" "flwr==$FLWR_VERSION" "numpy==$NUMPY_VERSION" \
    "safetensors==$SAFETENSORS_VERSION"
  if [ "$FLOWER_TLS" = 1 ]; then flower_check_ca; flower_keygen; fi
  log "Flower $FLWR_VERSION SuperNode ready: SuperLink $SUPERLINK, local port $FLOWER_LOCAL_PORT"
}

fw_start() {
  [ -x "$VENV/bin/flower-supernode" ] || die "run ./install.sh first"
  if pid_alive "$RUN/supernode.pid"; then log "already running (pid $(cat "$RUN/supernode.pid"))"; return 0; fi
  if [ "$FLOWER_TLS" = 1 ]; then
    flower_check_ca
    [ -f "$NODE_KEY" ] || die "no SuperNode key $NODE_KEY: run ./install.sh first"
  fi
  tcp_open "$CENTRAL_IP" "$FLOWER_FLEET_PORT" || die "cannot reach the SuperLink $SUPERLINK (firewall?)"
  rm -f "$RUN/supernode.pid"   # the loop writes its own pid (setsid may fork)
  setsid nohup bash "$KIT/flower_supernode.sh" >> "$RUN/supernode.log" 2>&1 < /dev/null &
  log "starting (data $DATA_DIR/<variant>-$SITE.csv, log $RUN/supernode.log)"
  for _ in $(seq 1 60); do
    if fw_status >/dev/null 2>&1; then log "connected to the SuperLink $SUPERLINK"; return 0; fi
    if last_start_log | grep -q "code: 200[14]"; then
      die "the SuperLink refused this node's key: has the operator registered $NODE_KEY.pub? (see $RUN/supernode.log; the loop keeps retrying)"
    fi
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
  # a refused node (unregistered key) opens the TCP connection and then exits: judge the last start
  if last_start_log | grep -qa "Exit Code"; then
    echo "$SITE: REFUSED by the SuperLink ($(last_start_log | grep -ao 'code: [0-9]*\] [^.]*' | tail -1)); restarting"; return 1
  fi
  node=$(last_start_log | grep -ao 'SuperNode ID: [0-9]*' | tail -1)
  [ -n "$node" ] || { echo "$SITE: NOT CONNECTED yet (pid $pid, no SuperNode ID since the last start)"; return 1; }
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

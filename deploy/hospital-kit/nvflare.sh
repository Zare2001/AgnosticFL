# shellcheck shell=bash
# NVFlare hospital (sourced as framework.sh): the standard NVFlare client, run from this site's
# startup kit (nvflare/<site>/startup: certificate, private key, server address), which ./install.sh
# builds here from the site's own key and the operator's signed certificate. The kit folder is the
# client's workspace. Nothing of the hub is installed: the training
# code (client.py + flhub_core) arrives as the custom code of the operator's job.
WS="$KIT/nvflare/$SITE"

nvf_target() {  # host:port of the NVFlare server, from the kit
  "$VENV/bin/python" -c 'import json,sys; print(json.load(open(sys.argv[1]))["servers"][0]["service"]["target"])' \
    "$WS/startup/fed_client.json"
}

# Distributed provisioning: this hospital makes its own private key and certificate request; the
# operator signs it; the startup kit is built here. Only public files travel.
REQ="$KIT/nvflare/request"

nvf_request() {  # key + request zip (once); prints what to send to the operator
  if [ ! -f "$REQ/$SITE.request.zip" ] || [ ! -f "$REQ/$SITE.key" ]; then
    local num
    num=$(sed -n 's/^nvf-h\([0-9][0-9]*\).*/\1/p' <<<"$SITE")
    [ -n "$num" ] || die "site name $SITE is not nvf-h<N>"
    rm -rf "$REQ" && mkdir -p "$REQ" && chmod 700 "$REQ"
    printf 'name: %s\nparticipants:\n  - name: %s\n    type: client\n    org: hospital%s\n' \
      "$NVFLARE_PROJECT" "$SITE" "$num" > "$REQ/participant.yaml"
    "$VENV/bin/nvflare" cert request -p "$REQ/participant.yaml" --out "$REQ" --force > "$REQ/request.log" 2>&1 \
      || { tail -5 "$REQ/request.log" >&2; die "nvflare cert request failed"; }
    chmod 600 "$REQ/$SITE.key"
  fi
  cp "$REQ/$SITE.request.zip" "$KIT/nvflare/$SITE.request.zip"
  local pub
  pub=$("$VENV/bin/python" - "$KIT/nvflare/$SITE.request.zip" "$SITE" <<'PY'
import hashlib, sys, zipfile
from cryptography import x509
from cryptography.hazmat.primitives import serialization
zf = zipfile.ZipFile(sys.argv[1])
for i in zf.infolist():
    if i.filename.endswith(".key") or b"PRIVATE KEY" in zf.read(i):
        sys.exit(f"{i.filename} in the request zip holds a private key; do not send it")
csr = x509.load_pem_x509_csr(zf.read(f"{sys.argv[2]}.csr"))
der = csr.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
print(hashlib.sha256(der).hexdigest())
PY
) || die "request zip check failed"
  log "certificate request: $KIT/nvflare/$SITE.request.zip (no private key in it)"
  log "public key sha256: $pub"
  log "next: send the operator the request zip AND this hash (by a second channel if you can);"
  log "      put the $SITE.signed.zip you get back into $KIT/nvflare/ and run ./install.sh again"
}

nvf_package() {  # startup kit from the operator's signed zip + this site's own key
  [ -n "${NVFLARE_CA_FINGERPRINT:-}" ] || die "kit.env has no NVFLARE_CA_FINGERPRINT"
  [ -f "$REQ/$SITE.key" ] || die "no private key in $REQ: the signed zip must answer this kit's own request"
  local tmp kit
  tmp=$(mktemp -d); chmod 700 "$tmp"
  "$VENV/bin/nvflare" package "$KIT/nvflare/$SITE.signed.zip" -w "$tmp/ws" --request-dir "$REQ" \
    --fingerprint "$NVFLARE_CA_FINGERPRINT" > "$tmp/package.log" 2>&1 \
    || { tail -5 "$tmp/package.log" >&2; rm -rf "$tmp"; die "nvflare package refused the signed zip"; }
  kit=$(ls -d "$tmp"/ws/*/prod_*/"$SITE" 2>/dev/null | head -1)
  [ -n "$kit" ] && [ -f "$kit/startup/fed_client.json" ] || { rm -rf "$tmp"; die "no client kit for $SITE in the signed zip"; }
  cmp -s "$kit/startup/client.key" "$REQ/$SITE.key" || { rm -rf "$tmp"; die "the kit's key is not this site's own request key"; }
  rm -rf "$WS" && mv "$kit" "$WS" && rm -rf "$tmp"
  chmod -R go-rwx "$WS"
  rm -f "$REQ/$SITE.key" "$KIT/nvflare/$SITE.signed.zip" "$KIT/nvflare/$SITE.request.zip"
  log "startup kit built here: $WS (root CA $NVFLARE_CA_FINGERPRINT)"
}

fw_install() {
  make_venv
  pip_torch
  uv pip install -q --python "$VENV/bin/python" "nvflare==$NVFLARE_VERSION" "numpy==$NUMPY_VERSION" \
    "safetensors==$SAFETENSORS_VERSION"
  # an installed flhub_core would shadow the copy that comes with each job (NVFlare appends custom/ to sys.path)
  if (cd / && "$VENV/bin/python" -c 'import importlib.util as u, sys; sys.exit(0 if u.find_spec("flhub_core") else 1)'); then
    uv pip uninstall -q --python "$VENV/bin/python" flhub-core
  fi
  if [ ! -f "$WS/startup/fed_client.json" ]; then
    if [ -f "$KIT/nvflare/$SITE.signed.zip" ]; then
      nvf_package
    else
      nvf_request
      exit 0  # waiting for the operator's signed zip
    fi
  fi
  local target host ip
  target=$(nvf_target); host=${target%:*}
  ip=$(getent ahostsv4 "$host" | awk '{print $1; exit}')
  if [ -z "$ip" ]; then
    log "adding '$CENTRAL_IP $host' to /etc/hosts (the kit's server name; needs sudo)"
    echo "$CENTRAL_IP $host" | sudo tee -a /etc/hosts >/dev/null
  elif [ "$ip" != "$CENTRAL_IP" ]; then
    log "warning: $host resolves to $ip, not to the operator's $CENTRAL_IP (check /etc/hosts)"
  fi
  chmod -R go-rwx "$WS"
  log "NVFlare $NVFLARE_VERSION client ready: workspace $WS, server $target"
}

fw_start() {
  export PATH="$VENV/bin:$PATH"
  [ -x "$VENV/bin/python" ] || die "run ./install.sh first"
  if pid_alive "$WS/daemon_pid.fl"; then log "already running"; return 0; fi
  local target lines
  target=$(nvf_target)
  tcp_open "${target%:*}" "${target##*:}" || die "cannot reach the NVFlare server $target (firewall? /etc/hosts?)"
  rm -f "$WS/shutdown.fl"
  lines=0; [ -f "$WS/log.txt" ] && lines=$(wc -l < "$WS/log.txt")
  # FLHUB_DATA_PATH: this site's data; data variants are <variant>-<site>.csv next to it
  (cd "$WS" && FLHUB_DATA_PATH="$DATA_DIR/$SITE.csv" setsid nohup bash startup/start.sh \
      > "$RUN/start.out" 2>&1 < /dev/null &)
  log "starting (data $DATA_DIR/<variant>-$SITE.csv)"
  for _ in $(seq 1 90); do
    if awk -v from="$((lines + 1))" 'NR >= from && /Successfully registered client/ {ok = 1} END {exit !ok}' \
        "$WS/log.txt" 2>/dev/null; then
      log "registered with the NVFlare server $target"; return 0
    fi
    sleep 1
  done
  die "not registered after 90 s; see $WS/log.txt"
}

fw_stop() {
  if pid_alive "$WS/daemon_pid.fl"; then
    local dpid
    dpid=$(cat "$WS/daemon_pid.fl")
    touch "$WS/shutdown.fl"   # the kit's own stop mechanism (startup/stop_fl.sh, without its prompt)
    for _ in $(seq 1 40); do kill -0 "$dpid" 2>/dev/null || break; sleep 1; done
    kill -0 "$dpid" 2>/dev/null && die "client daemon $dpid still alive"
    log "stopped"
  else
    log "not running"
  fi
  pkill -f "nvflare.private.fed.app.client.worker_process.*$WS/" 2>/dev/null || true
  rm -f "$WS/shutdown.fl"
}

fw_status() {
  local target pid conns jobs
  target=$(nvf_target 2>/dev/null || echo "?:?")
  if ! pid_alive "$WS/pid.fl"; then echo "$SITE: DOWN (no NVFlare client process; server $target)"; return 1; fi
  pid=$(cat "$WS/pid.fl")
  command -v ss >/dev/null || { echo "$SITE: up, NVFlare client pid $pid (connection not checked: no ss)"; return 0; }
  conns=$(ss -Htnp state established "( dport = :${target##*:} )" 2>/dev/null | grep -c "pid=$pid," || true)
  jobs=$(pgrep -f "worker_process.*$WS/" | wc -l)
  if [ "$conns" -ge 1 ]; then
    echo "$SITE: up, NVFlare client pid $pid connected to $target, job processes: $jobs"
  else
    echo "$SITE: NOT CONNECTED (pid $pid, server $target)"; return 1
  fi
}

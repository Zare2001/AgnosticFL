#!/usr/bin/env bash
# Distributed provisioning of the NVFlare project, central side (NVFlare 2.9 `nvflare cert`):
#  1. root CA, once (`nvflare cert init`): $NVF_CA_DIR, mode 700;
#  2. the server ($NVF_SERVER_NAME, fed $NVF_FL_PORT, admin $NVF_ADMIN_PORT) and the adapter's admin
#     identity ($NVF_ADMIN_USER, role $NVF_ADMIN_ROLE) each make a request, central approves it, and
#     `nvflare package` builds their kits in $NVF_PROD (same paths as the stage-1 provisioning).
# Hospital kits are NOT made here: each hospital runs deploy/nvflare-vm/request.sh, central signs the
# request with approve.sh, the hospital builds its own kit (deploy/nvflare-vm/install.sh).
# Idempotent: an existing CA and existing kits are kept. A new CA means new kits everywhere; to make
# one, move $NVF_CA_DIR and $NVF_WS away first.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/common.sh"
NVF="$NVF_VENV/bin/nvflare"
mkdir -p "$NVF_RUNTIME" && chmod 700 "$NVF_RUNTIME"
mkdir -p "$NVF_REQ_DIR" "$NVF_SIGNED_OUT" "$NVF_ISSUED" && chmod 700 "$NVF_REQ_DIR" "$NVF_SIGNED_OUT" "$NVF_ISSUED"

cat > "$NVF_PROFILE" <<EOF
name: $NVF_PROJECT
scheme: $NVF_SCHEME
connection_security: $NVF_CONN_SEC
server:
  host: $NVF_SERVER_NAME
  fed_learn_port: $NVF_FL_PORT
  admin_port: $NVF_ADMIN_PORT
EOF

run() {  # log file, command...: quiet on success, tail of the log on failure
  local log=$1; shift
  "$@" > "$log" 2>&1 || { tail -20 "$log" >&2; exit 1; }
}

if [ -f "$NVF_CA_DIR/ca.json" ]; then
  echo "provision: kept root CA in $NVF_CA_DIR"
else
  [ -e "$NVF_CA_DIR" ] && { echo "provision: $NVF_CA_DIR exists without ca.json; move it away" >&2; exit 1; }
  # No --org: NVFlare 2.9.0 `cert approve` writes the issuer as CN only, so a CA subject with O=...
  # breaks the chain (openssl verify: "unable to get local issuer certificate").
  run "$NVF_RUNTIME/cert-init.log" "$NVF" cert init --profile "$NVF_PROFILE" -o "$NVF_CA_DIR"
  chmod 700 "$NVF_CA_DIR"
  echo "provision: new root CA in $NVF_CA_DIR"
fi
fp=$(nvf_ca_fingerprint)
echo "$fp" > "$NVF_RUNTIME/rootCA.fingerprint"

kit_for() {  # name, participant yaml body, kit dir: request + approve + package unless the kit exists
  local name=$1 body=$2 kit=$3 req="$NVF_REQ_DIR/$1"
  if [ -d "$kit/startup" ]; then
    if [ "$(sha256sum < "$kit/startup/rootCA.pem")" != "$(sha256sum < "$NVF_CA_DIR/rootCA.pem")" ]; then
      echo "provision: $kit has a different root CA; move $NVF_WS away to rebuild the kits" >&2; exit 1
    fi
    echo "provision: kept kit $kit"; return
  fi
  rm -rf "$req" && mkdir -p "$req" && chmod 700 "$req"
  printf 'name: %s\nparticipants:\n%s\n' "$NVF_PROJECT" "$body" > "$req/participant.yaml"
  run "$req/request.log" "$NVF" cert request -p "$req/participant.yaml" --out "$req/out"
  run "$req/approve.log" "$NVF" cert approve "$req/out/$name.request.zip" -c "$NVF_CA_DIR" \
    --profile "$NVF_PROFILE" --out "$req/$name.signed.zip" --force
  run "$req/package.log" "$NVF" package "$req/$name.signed.zip" -w "$NVF_WS" --request-dir "$req/out" \
    --fingerprint "$fp" --force
  [ -d "$kit/startup" ] || { echo "provision: no kit in $kit after packaging" >&2; exit 1; }
  rm -f "$req/out/$name.key"  # the kit holds the key now; keep one copy
  echo "provision: new kit $kit"
}

kit_for "$NVF_SERVER_NAME" "  - name: $NVF_SERVER_NAME
    type: server
    org: flhub
    fed_learn_port: $NVF_FL_PORT
    admin_port: $NVF_ADMIN_PORT" "$NVF_SERVER_KIT"
kit_for "$NVF_ADMIN_USER" "  - name: $NVF_ADMIN_USER
    type: admin
    org: flhub
    role: $NVF_ADMIN_ROLE" "$NVF_ADMIN_KIT"

# The template keeps the server's job store and snapshots in /tmp/nvflare/, shared by every NVFlare
# server on this host (a test project would see the live jobs) and cleared by a reboot. Each project
# gets its own under $NVF_RUNTIME/storage (local/resources.json overrides resources.json.default).
res="$NVF_SERVER_KIT/local/resources.json"
mkdir -p "$NVF_RUNTIME/storage" && chmod 700 "$NVF_RUNTIME/storage"
"$NVF_VENV/bin/python" - "$res.default" "$res" "$NVF_RUNTIME/storage" <<'PY'
import json, os, sys
src, dst, root = sys.argv[1], sys.argv[2], sys.argv[3]
cfg = json.load(open(dst if os.path.exists(dst) else src))
def fix(o):
    if isinstance(o, dict): return {k: fix(v) for k, v in o.items()}
    if isinstance(o, list): return [fix(v) for v in o]
    if isinstance(o, str) and o.startswith("/tmp/nvflare/"): return os.path.join(root, o[len("/tmp/nvflare/"):])
    return o
out = fix(cfg)
text = json.dumps(out, indent=2)
if "/tmp/nvflare" in text: sys.exit("provision: /tmp/nvflare still referenced in " + dst)
for need in ("jobs-storage", "snapshot-storage"):
    if os.path.join(root, need) not in text: sys.exit(f"provision: {need} not under {root} in {dst}")
open(dst, "w").write(text + "\n")
PY
echo "provision: server job store and snapshots in $NVF_RUNTIME/storage"
for crt in "$NVF_SERVER_KIT/startup/server.crt" "$NVF_ADMIN_KIT/startup/client.crt"; do
  openssl verify -CAfile "$NVF_CA_DIR/rootCA.pem" "$crt" >/dev/null \
    || { echo "provision: $crt does not verify against the root CA" >&2; exit 1; }
done
echo "provision: project $NVF_PROJECT, server $NVF_SERVER_NAME:$NVF_FL_PORT, root CA fingerprint"
echo "provision:   $fp"
echo "provision: publish it to the hospitals (bus); sign each hospital request with approve.sh"

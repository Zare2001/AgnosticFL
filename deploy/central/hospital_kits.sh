#!/usr/bin/env bash
# Operator, central machine: one self-contained kit per hospital, to hand over a secure channel.
#   bash deploy/central/hospital_kits.sh [--out DIR] [--variants "cifar10-iid ..."] [site ...]
# Default: the six base sites of deploy/topology.env, demo data variant cifar10-iid, output
# ~/flhub-runtime/hospital-kits/<site>.tgz (mode 600; it holds the site's credentials).
#
# A kit contains the hospital's credentials for its framework (NVFlare: none; the hospital makes its
# own key and certificate request with the kit and gets back a certificate, see below; vantage6: the node API key; Flower: none), install/start/stop/status scripts for
# the framework's STANDARD client with the pinned versions, the operator's address, and a demo data
# tool (a copy of flhub_core, used with numpy only) with the partition manifests to verify it.
# Run the framework's central install.sh first (NVFlare root CA, vantage6 keys); extra vantage6 sites:
# deploy/central/vantage6/add_sites.sh. NVFlare: the kit carries the project name and the root CA
# fingerprint (both public); ./install.sh at the hospital makes the key and <site>.request.zip, the
# operator signs it with deploy/central/nvflare/approve.sh, ./install.sh again builds the startup kit.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
. "$REPO/deploy/central/nvflare/common.sh"   # NVF_WS, NVF_PROJECT (+ topology.env, versions.env)
OUT="$HOME/flhub-runtime/hospital-kits"
VARIANTS="cifar10-iid"
V6_RT="${V6_RT:-$HOME/flhub-runtime/vantage6}"
while [ $# -gt 0 ]; do
  case "$1" in
    --out) OUT="$2"; shift 2 ;;
    --variants) VARIANTS="$2"; shift 2 ;;
    -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
    -*) echo "unknown option $1" >&2; exit 2 ;;
    *) break ;;
  esac
done
sites=("$@")
if [ ${#sites[@]} -eq 0 ]; then
  for s in $SITES; do sites+=("${s%%:*}"); done
fi

framework_of() {  # topology SITES entry, or the site name's prefix for the extra sites
  local s id fw
  for s in $SITES; do IFS=: read -r id fw _ <<<"$s"; [ "$id" = "$1" ] && { echo "$fw"; return; }; done
  case "$1" in nvf-*) echo nvflare ;; v6-*) echo vantage6 ;; flw-*) echo flower ;; *) echo "" ;; esac
}
v6_key_of() {
  local var f
  var="V6_API_KEY_$(echo "$1" | tr 'a-z-' 'A-Z_')"
  for f in "$V6_RT/secrets.env" "$V6_RT/v6-nodes-extra.env"; do
    [ -f "$f" ] && grep -E "^$var=" "$f" | tail -1 | cut -d= -f2- && return
  done
  return 0
}
flower_port_of() {  # the SuperNode's local port, as on the testbed's Flower machine (flw-h5 9094, flw-h6 9095, ...)
  case "$1" in flw-h5) echo 9094 ;; flw-h6) echo 9095 ;; flw-h11) echo 9096 ;; flw-h12) echo 9097 ;; *) echo 9094 ;; esac
}

src="$(git -C "$REPO" rev-parse --short=12 HEAD 2>/dev/null || echo unknown)"
core="$(PYTHONPATH="$REPO/flhub_core/src" python3 -c 'import flhub_core; print(flhub_core.code_version())')"
mkdir -p "$OUT" && chmod 700 "$OUT"
stage="$(mktemp -d)"; trap 'rm -rf "$stage"' EXIT
made=()
for site in "${sites[@]}"; do
  fw="$(framework_of "$site")"
  [ -n "$fw" ] || { echo "kits: $site: unknown framework (not in topology SITES)" >&2; exit 1; }
  kit="$stage/flhub-$site"
  mkdir -p "$kit"
  cp "$REPO"/deploy/hospital-kit/{lib.sh,install.sh,start.sh,stop.sh,status.sh,prepare_data.sh} "$kit/"
  cp "$REPO/deploy/hospital-kit/$fw.sh" "$kit/framework.sh"
  case "$fw" in
    nvflare)
      [ -s "$NVF_RUNTIME/rootCA.fingerprint" ] || { echo "kits: $site: no NVFlare root CA (run deploy/central/nvflare/install.sh)" >&2; exit 1; }
      mkdir -p "$kit/nvflare" ;;
    vantage6)
      key="$(v6_key_of "$site")"
      [ -n "$key" ] || { echo "kits: $site: no vantage6 node API key in $V6_RT (run deploy/central/vantage6/install.sh or add_sites.sh)" >&2; exit 1; }
      mkdir -p "$kit/credentials" && (umask 077; printf '%s\n' "$key" > "$kit/credentials/api_key")
      cp "$REPO/deploy/vantage6-vm/node.template.yaml" "$kit/" ;;
    flower)
      cp "$REPO/deploy/hospital-kit/flower_supernode.sh" "$kit/" ;;
  esac
  mkdir -p "$kit/data-tool" "$kit/manifests"
  tar -C "$REPO/flhub_core/src" --exclude=__pycache__ -cf - flhub_core | tar -C "$kit/data-tool" -xf -
  for v in $VARIANTS; do
    if [ -f "$REPO/configs/stage2/partitions/$v.json" ]; then
      cp "$REPO/configs/stage2/partitions/$v.json" "$kit/manifests/"
    else
      echo "kits: warning: no manifest configs/stage2/partitions/$v.json (python -m flhub_core.data manifests --variants $v --out configs/stage2/partitions)" >&2
    fi
  done
  cat > "$kit/kit.env" <<EOF
# AgnosticFL hospital kit for site $site, made $(date -u +%FT%TZ) from $src.
SITE=$site
FRAMEWORK=$fw
CENTRAL_IP=$FLHUB_CENTRAL_IP
KIT_SOURCE=$src
DEMO_VARIANTS="$VARIANTS"
# operator's ports (only the one of this framework is used)
NVFLARE_FL_PORT=$NVFLARE_FL_PORT
NVFLARE_PROJECT=$NVF_PROJECT
NVFLARE_CA_FINGERPRINT=$(cat "$NVF_RUNTIME/rootCA.fingerprint" 2>/dev/null)
V6_SERVER_PORT=$V6_SERVER_PORT
REGISTRY_PORT=$REGISTRY_PORT
FLOWER_FLEET_PORT=$FLOWER_FLEET_PORT
FLOWER_LOCAL_PORT=$(flower_port_of "$site")
# pinned versions (deploy/versions.env): every machine of the federation runs the same numerics
PYTHON_VERSION=$PYTHON_VERSION
TORCH_VERSION=$TORCH_VERSION
TORCH_INDEX_URL=$TORCH_INDEX_URL
NUMPY_VERSION=$NUMPY_VERSION
SAFETENSORS_VERSION=$SAFETENSORS_VERSION
NVFLARE_VERSION=$NVFLARE_VERSION
VANTAGE6_VERSION=$VANTAGE6_VERSION
FLWR_VERSION=$FLWR_VERSION
V6_NODE_IMAGE=$V6_NODE_IMAGE
EOF
  req=""; data_note=""; logs="\`run/\`"
  case "$fw" in
    nvflare)
      creds="none in the kit. \`./install.sh\` makes this hospital's private key here (it never leaves this machine) and \`nvflare/$site.request.zip\`. Send the operator that zip and the public key hash that install.sh prints; put the \`$site.signed.zip\` you get back into \`nvflare/\` and run \`./install.sh\` again: it builds the startup kit \`nvflare/$site/\` and checks the operator's root CA fingerprint ($(cat "$NVF_RUNTIME/rootCA.fingerprint" 2>/dev/null))"; port="$NVFLARE_FL_PORT"
      req="sudo once, to add the server name of the startup kit to /etc/hosts if it does not resolve."
      logs="\`run/\` and \`nvflare/$site/log.txt\`" ;;
    vantage6)
      creds="credentials/api_key (this node's vantage6 API key)"; port="$V6_SERVER_PORT and $REGISTRY_PORT"
      req="Docker, usable by this user without sudo (\`sudo usermod -aG docker \$USER\`, then log in again); sudo once, to allow the operator's image registry in Docker."
      data_note="The node offers the files that exist when it starts: after adding files run \`./stop.sh && ./start.sh\`."
      logs="\`run/\` and \`docker logs -f vantage6-$site-user\`" ;;
    flower)
      creds="none (Flower runs without client credentials on this testbed)"; port="$FLOWER_FLEET_PORT"
      logs="\`run/supernode.log\`" ;;
  esac
  cat > "$kit/README.md" <<EOF
# AgnosticFL hospital kit: $site ($fw)

This kit connects one hospital, site **$site**, to the operator's federation at **$FLHUB_CENTRAL_IP**.
It installs the standard **$fw** client only, with the operator's credentials and the pinned
versions in \`kit.env\`. The training code (model, data loading, training loop) is not installed
here: it reaches the client through $fw's own channel each time the operator starts a run, so
changes of model or data settings need nothing at this hospital.

Credentials: $creds. Keep this folder private (it is mode 700 after install).

## Requirements

Linux x86_64 (Ubuntu 22.04 or 24.04), bash, curl, outbound TCP to $FLHUB_CENTRAL_IP port $port.
$req

## Steps

\`\`\`bash
tar -xzf $site.tgz && cd flhub-$site     # where it should live
./install.sh          # Python env with the $fw client, credentials in place
./prepare_data.sh     # demo only: this site's share of CIFAR-10 -> ~/flhub-data/cifar10-iid-$site.csv
./start.sh            # starts the client and waits until it is connected
./status.sh           # HEALTHY = running and connected
./stop.sh
\`\`\`

## Data

The client reads \`<variant>-$site.csv\` files in \`\$FLHUB_DATA_DIR\` (default \`~/flhub-data\`); the operator
names the variant per run (e.g. \`cifar10-iid\`). Own data: one CSV per variant with the header
\`label,x0..x3071\` (32x32 RGB images, the R, G and B planes one after another, integer pixels 0-255,
integer class labels) or \`label,x0..x783\` (28x28 grayscale). The data never leaves this machine;
only model weights do. $data_note

Logs: $logs.

More: https://github.com/Zare2001/AgnosticFL/blob/main/docs/guide.md#7-for-hospitals-your-kit
EOF
  chmod 755 "$kit"/*.sh && chmod 644 "$kit/lib.sh" "$kit/framework.sh"
  chmod 700 "$kit"
  tar -czf "$OUT/$site.tgz" -C "$stage" "flhub-$site"
  chmod 600 "$OUT/$site.tgz"
  made+=("$site ($fw)")
  echo "kits: $site ($fw) -> $OUT/$site.tgz"
done
echo "kits: flhub_core $core, demo data: $VARIANTS"
rel="${OUT#"$HOME"/}"
echo "kits: hand each kit to its hospital over a secure channel, e.g. from your laptop:"
echo "        scp -3 $(whoami)@$FLHUB_CENTRAL_IP:$rel/<site>.tgz <user>@<hospital>:"
echo "      the hospital runs: tar -xzf <site>.tgz && cd flhub-<site> && ./install.sh && ./prepare_data.sh && ./start.sh"

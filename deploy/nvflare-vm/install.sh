#!/usr/bin/env bash
# Installs the NVFlare hospitals on this VM (idempotent):
#  1. venv with the standard NVFlare client + torch + flhub_core (nothing hub-specific)
#  2. each hospital's data file ~/flhub-data/<site>.csv (sha256-verified partition)
#  3. startup kits from the operator: install.sh [kit.tgz ...]  (default: $NVF_INCOMING/*.tgz)
#     A kit replaces the site's previous kit; the archive is deleted after installing.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/common.sh"

command -v uv >/dev/null || { echo "install: uv not found on PATH" >&2; exit 1; }
[ -x "$NVF_VENV/bin/python" ] || uv venv -q --python "$PYTHON_VERSION" "$NVF_VENV"
PY="$NVF_VENV/bin/python"
uv pip install -q --python "$PY" "torch==${TORCH_VERSION}+cpu" --index-url "$TORCH_INDEX_URL"
uv pip install -q --python "$PY" "nvflare==$NVFLARE_VERSION" "numpy==$NUMPY_VERSION" \
  "safetensors==$SAFETENSORS_VERSION" -e "$REPO/flhub_core"
echo "install: venv $NVF_VENV: $("$PY" -c 'import nvflare, torch, flhub_core; print("nvflare", nvflare.__version__, "torch", torch.__version__, "flhub_core", flhub_core.code_version())')"

FLHUB_DATA_DIR="$FLHUB_DATA_DIR" "$PY" -m flhub_core.data materialize --sites $NVF_SITES --out "$FLHUB_DATA_DIR" \
  | sed 's/^/install: data /'

kits=("$@")
if [ ${#kits[@]} -eq 0 ] && compgen -G "$NVF_INCOMING/*.tgz" >/dev/null; then kits=("$NVF_INCOMING"/*.tgz); fi
mkdir -p "$NVF_SITES_DIR" && chmod 700 "$NVF_SITES_DIR"
for kit in "${kits[@]}"; do
  tmp=$(mktemp -d)
  tar -xzf "$kit" -C "$tmp"
  site=$(ls "$tmp")
  if [ "$(wc -w <<<"$site")" != 1 ] || [ ! -f "$tmp/$site/startup/fed_client.json" ]; then
    echo "install: $kit is not a single NVFlare client kit" >&2; rm -rf "$tmp"; exit 1
  fi
  if ! grep -qw -- "$site" <<<"$NVF_SITES"; then
    echo "install: kit for unexpected site '$site' (expected: $NVF_SITES)" >&2; rm -rf "$tmp"; exit 1
  fi
  if nvf_pid_alive "$NVF_SITES_DIR/$site/daemon_pid.fl"; then
    echo "install: $site is running; stop it first (stop.sh $site)" >&2; rm -rf "$tmp"; exit 1
  fi
  rm -rf "${NVF_SITES_DIR:?}/$site"
  mv "$tmp/$site" "$NVF_SITES_DIR/$site" && rm -rf "$tmp"
  chmod -R go-rwx "$NVF_SITES_DIR/$site"
  rm -f "$kit"
  target=$("$PY" -c 'import json,sys; print(json.load(open(sys.argv[1]))["servers"][0]["service"]["target"])' \
    "$NVF_SITES_DIR/$site/startup/fed_client.json")
  echo "install: kit $site -> $NVF_SITES_DIR/$site (server $target)"
done
for site in $NVF_SITES; do
  [ -d "$NVF_SITES_DIR/$site/startup" ] || echo "install: no kit yet for $site (waiting for the operator)"
done
echo "install: OK"

#!/usr/bin/env bash
# Installs the NVFlare hospitals on this VM (idempotent):
#  1. venv with the standard NVFlare client + torch, numpy, safetensors. NO flhub_core: the training
#     code (client.py + flhub_core) comes with each NVFlare job as custom code (ADR-018); NVFlare
#     appends a job's custom/ to sys.path, so an installed flhub_core would shadow the job's copy.
#  2. data, sha256-verified: the stage-1 file <site>.csv and the stage-2 variant files
#     <variant>-<site>.csv next to it (configs/stage2/partitions manifests), made with this checkout's
#     flhub_core as a provisioning tool.
#  3. startup kits from the operator: install.sh [kit.tgz ...]  (default: $NVF_INCOMING/*.tgz)
#     A kit replaces the site's previous kit; the archive is deleted after installing.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/common.sh"

command -v uv >/dev/null || { echo "install: uv not found on PATH" >&2; exit 1; }
[ -x "$NVF_VENV/bin/python" ] || uv venv -q --python "$PYTHON_VERSION" "$NVF_VENV"
PY="$NVF_VENV/bin/python"
uv pip install -q --python "$PY" "torch==${TORCH_VERSION}+cpu" --index-url "$TORCH_INDEX_URL"
uv pip install -q --python "$PY" "nvflare==$NVFLARE_VERSION" "numpy==$NUMPY_VERSION" "safetensors==$SAFETENSORS_VERSION"
if (cd / && "$PY" -c 'import importlib.util as u, sys; sys.exit(0 if u.find_spec("flhub_core") else 1)' 2>/dev/null); then
  uv pip uninstall -q --python "$PY" flhub-core
  echo "install: removed flhub_core from $NVF_VENV (it comes with the jobs); restart the clients"
fi
echo "install: venv $NVF_VENV: $("$PY" -c 'import nvflare, torch; print("nvflare", nvflare.__version__, "torch", torch.__version__)')"

nvf_data materialize --sites $NVF_SITES --out "$FLHUB_DATA_DIR" | sed 's/^/install: data /'
nvf_data materialize --variants all --sites $NVF_SITES --out "$FLHUB_DATA_DIR" \
  --manifests "$REPO/configs/stage2/partitions" | sed 's/^/install: data /'

kits=("$@")
if [ ${#kits[@]} -eq 0 ] && compgen -G "$NVF_INCOMING/*.tgz" >/dev/null; then kits=("$NVF_INCOMING"/*.tgz); fi
for kit in "${kits[@]}"; do nvf_install_kit "$kit" "$NVF_SITES"; done
for site in $NVF_SITES; do
  [ -d "$NVF_SITES_DIR/$site/startup" ] || echo "install: no kit yet for $site (waiting for the operator)"
done
echo "install: OK"

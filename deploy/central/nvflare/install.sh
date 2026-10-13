#!/usr/bin/env bash
# Installs the NVFlare side of the hub on the central VM (idempotent):
# venv with pinned versions + flhub_core + hub package, provisioning, relay job folder.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/common.sh"

command -v uv >/dev/null || { echo "install: uv not found on PATH" >&2; exit 1; }
[ -x "$NVF_VENV/bin/python" ] || uv venv -q --python "$PYTHON_VERSION" "$NVF_VENV"
PY="$NVF_VENV/bin/python"
# torch first from the CPU index (flhub_core depends on it), then the rest from PyPI
uv pip install -q --python "$PY" "torch==${TORCH_VERSION}+cpu" --index-url "$TORCH_INDEX_URL"
uv pip install -q --python "$PY" "nvflare==$NVFLARE_VERSION" "numpy==$NUMPY_VERSION" \
  "safetensors==$SAFETENSORS_VERSION" -e "$REPO/flhub_core" -e "$REPO"
echo "install: venv $NVF_VENV: $("$PY" -c 'import nvflare, torch; print("nvflare", nvflare.__version__, "torch", torch.__version__)')"

bash "$HERE/provision.sh"

mkdir -p "$NVF_JOBS" "$NVF_LOGS"
rm -rf "${NVF_JOBS:?}/flhub-relay"
n_sites=$(wc -w <<<"$NVF_SITES")
job_dir=$("$PY" -m adapters.nvflare.job --out "$NVF_JOBS" --hub-url "$NVF_HUB_URL" \
  --adapter-url "http://127.0.0.1:$NVF_ADAPTER_PORT" --min-clients "$n_sites")
echo "install: relay job folder $job_dir"
echo "install: OK"

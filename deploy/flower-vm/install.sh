#!/usr/bin/env bash
# Install the Flower hospital side on the flower VM (idempotent): venv with torch (CPU),
# flwr and flhub_core, plus the flw-h5 / flw-h6 data partitions in ~/flhub-data.
set -euo pipefail
. "$(dirname "$0")/common.sh"

[ -x "$VENV/bin/python" ] || uv venv --python "$PYTHON_VERSION" "$VENV"
uv pip install --python "$VENV/bin/python" "torch==${TORCH_VERSION}+cpu" --index-url "$TORCH_INDEX_URL"
uv pip install --python "$VENV/bin/python" "flwr==$FLWR_VERSION" "numpy==$NUMPY_VERSION" \
    "safetensors==$SAFETENSORS_VERSION" -e "$REPO/flhub_core"
mkdir -p "$RUNTIME/logs" "$RUNTIME/run"
"$VENV/bin/python" -m flhub_core.data materialize --sites flw-h5 flw-h6 --out "$DATA_DIR"
"$VENV/bin/python" -c "import flwr, torch, flhub_core; print('flwr', flwr.__version__, \
'torch', torch.__version__, 'flhub_core', flhub_core.code_version())"
echo "install OK (venv $VENV, data $DATA_DIR)"

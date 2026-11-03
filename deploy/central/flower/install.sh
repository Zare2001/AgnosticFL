#!/usr/bin/env bash
# Install the Flower server side on the central VM (idempotent): venv with flwr, flhub_core
# (+ torch CPU, a flhub_core dependency) and the hub package (adapters.common, used by the
# adapter and the relay ServerApp), plus the flwr CLI connection to the local SuperLink.
set -euo pipefail
. "$(dirname "$0")/common.sh"

[ -x "$VENV/bin/python" ] || uv venv --python "$PYTHON_VERSION" "$VENV"
uv pip install --python "$VENV/bin/python" "torch==${TORCH_VERSION}+cpu" --index-url "$TORCH_INDEX_URL"
uv pip install --python "$VENV/bin/python" "flwr==$FLWR_VERSION" "numpy==$NUMPY_VERSION" \
    "safetensors==$SAFETENSORS_VERSION" -e "$REPO/flhub_core" -e "$REPO"
mkdir -p "$RUNTIME/logs" "$RUNTIME/run" "$FLWR_HOME_DIR"
write_flwr_config
"$VENV/bin/python" -c "import flwr, flhub_core; print('flwr', flwr.__version__, 'flhub_core', \
flhub_core.code_version())"
echo "install OK (venv $VENV, FLWR_HOME $FLWR_HOME_DIR)"

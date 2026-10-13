#!/usr/bin/env bash
# Central VM: hub venv, dataset (test split for global evaluation), Docker registry :5050,
# systemd unit for the hub core (127.0.0.1:8800). Idempotent.
set -euo pipefail
. "$(dirname "$0")/common.sh"
mkdir -p "$RUNTIME"/{hubcore,runs,registry,logs}

uv python install "$PYTHON_VERSION"
[ -x "$HUB_VENV/bin/python" ] || uv venv --python "$PYTHON_VERSION" "$HUB_VENV"
uv pip install --python "$HUB_VENV/bin/python" "torch==${TORCH_VERSION}+cpu" --index-url "$TORCH_INDEX_URL"
uv pip install --python "$HUB_VENV/bin/python" "numpy==$NUMPY_VERSION" "safetensors==$SAFETENSORS_VERSION" \
  "pyyaml==$PYYAML_VERSION" "wandb==$WANDB_VERSION" pandas pytest -e "$REPO/flhub_core" -e "$REPO"
"$HUB_VENV/bin/python" -c "from flhub_core.data import download; print('dataset', download())"

if ! docker inspect "$REGISTRY_NAME" >/dev/null 2>&1; then
  docker run -d --restart=always -p "$REGISTRY_PORT:5000" --name "$REGISTRY_NAME" \
    -v "$RUNTIME/registry:/var/lib/registry" "$REGISTRY_IMAGE"
fi

sudo tee "/etc/systemd/system/$HUB_UNIT.service" >/dev/null <<UNIT
[Unit]
Description=AgnosticFL hub core (blob store + model spec) on 127.0.0.1:$HUB_CORE_PORT
After=network.target

[Service]
User=$(id -un)
WorkingDirectory=$REPO
ExecStart=$HUB_VENV/bin/python -m hub.core --host 127.0.0.1 --port $HUB_CORE_PORT --root $RUNTIME/hubcore
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload
sudo systemctl enable "$HUB_UNIT" >/dev/null
echo "central install done"

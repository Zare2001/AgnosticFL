#!/usr/bin/env bash
# Install the Flower hospital side on the flower VM (idempotent): venv with torch (CPU),
# flwr and flhub_core, plus the flw-h5 / flw-h6 data partitions in ~/flhub-data.
# flhub_core is a fixed copy of $FLHUB_CORE_REF (default stage1-final = 0.1.0), not an editable
# link to this checkout: a git pull must not change hospital code. Newer flhub_core versions
# reach the hospitals inside the FAB (the ClientApp's dir comes first on sys.path).
set -euo pipefail
. "$(dirname "$0")/common.sh"

[ -x "$VENV/bin/python" ] || uv venv --python "$PYTHON_VERSION" "$VENV"
uv pip install --python "$VENV/bin/python" "torch==${TORCH_VERSION}+cpu" --index-url "$TORCH_INDEX_URL"
core_src="$(mktemp -d)"
git -C "$REPO" archive "${FLHUB_CORE_REF:-stage1-final}" flhub_core | tar -x -C "$core_src"
uv pip install --python "$VENV/bin/python" --reinstall-package flhub-core "flwr==$FLWR_VERSION" \
    "numpy==$NUMPY_VERSION" "safetensors==$SAFETENSORS_VERSION" "$core_src/flhub_core"
rm -rf "$core_src"
mkdir -p "$RUNTIME/logs" "$RUNTIME/run"
"$VENV/bin/python" -m flhub_core.data materialize --sites flw-h5 flw-h6 --out "$DATA_DIR"
"$VENV/bin/python" -c "import flwr, torch, flhub_core; print('flwr', flwr.__version__, \
'torch', torch.__version__, 'flhub_core', flhub_core.code_version())"
# Watchdog: re-run start.sh every minute while the nodes are enabled (start.sh enables, stop.sh
# disables), with the SuperLink address they were started with. start.sh is idempotent.
CRON="* * * * * [ -f $RUNTIME/nodes.enabled ] && FLHUB_SUPERLINK=\"\$(cat $RUNTIME/nodes.enabled)\" \
FLHUB_WATCHDOG=1 flock -n $RUNTIME/watchdog.lock bash $REPO/deploy/flower-vm/start.sh >> $RUNTIME/logs/watchdog.log 2>&1"
others="$(crontab -l 2>/dev/null | grep -v 'deploy/flower-vm/start.sh' || true)"
others="$others
$(crontab -l 2>/dev/null | grep 'deploy/flower-vm/start.sh' | grep -F 'FLHUB_FLOWER_RUNTIME=' || true)"  # extra.sh (E5)
printf '%s\n%s\n' "$others" "$CRON" | sed '/^$/d' | crontab -
echo "install OK (venv $VENV, data $DATA_DIR, watchdog cron entry installed)"

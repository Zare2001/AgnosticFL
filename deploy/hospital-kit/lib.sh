# shellcheck shell=bash disable=SC2034  # the variables are used by the scripts that source this file
# Shared settings and helpers of a hospital kit (sourced by install.sh, start.sh, stop.sh, status.sh,
# prepare_data.sh). kit.env, written by the operator's deploy/central/hospital_kits.sh, holds this
# hospital's site id, its framework, the operator's address and the pinned versions.
# Overrides from the environment: FLHUB_CENTRAL_IP, FLHUB_DATA_DIR, FLHUB_PYTHON_VERSION,
# FLHUB_TORCH_INDEX_URL (empty = torch from PyPI).
set -euo pipefail
KIT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
. "$KIT/kit.env"
CENTRAL_IP="${FLHUB_CENTRAL_IP:-$CENTRAL_IP}"
DATA_DIR="${FLHUB_DATA_DIR:-$HOME/flhub-data}"
PYTHON_VERSION="${FLHUB_PYTHON_VERSION:-$PYTHON_VERSION}"
TORCH_INDEX_URL="${FLHUB_TORCH_INDEX_URL-$TORCH_INDEX_URL}"
VENV="$KIT/venv"   # the framework's standard client and its pinned dependencies
RUN="$KIT/run"     # pid files and logs
export PATH="$HOME/.local/bin:$PATH"

log() { echo "[$SITE] $*"; }
die() { echo "[$SITE] ERROR: $*" >&2; exit 1; }
pid_alive() { [ -f "$1" ] && kill -0 "$(cat "$1")" 2>/dev/null; }
tcp_open() { timeout 3 bash -c "</dev/tcp/$1/$2" 2>/dev/null; }

ensure_uv() {
  if ! command -v uv >/dev/null; then
    log "installing uv (Python installer, https://astral.sh/uv) into ~/.local/bin"
    curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null
  fi
  command -v uv >/dev/null || die "uv is not on PATH after installing it"
}

make_venv() {
  [ -x "$VENV/bin/python" ] || uv venv -q --python "$PYTHON_VERSION" "$VENV"
}

pip_torch() {  # the pinned CPU build of torch, the same on every machine of the federation
  if [ -n "$TORCH_INDEX_URL" ]; then
    uv pip install -q --python "$VENV/bin/python" "torch==${TORCH_VERSION}+cpu" --index-url "$TORCH_INDEX_URL"
  else
    uv pip install -q --python "$VENV/bin/python" "torch==${TORCH_VERSION}"
  fi
}

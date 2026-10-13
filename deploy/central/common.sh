# Shared settings for the central hub scripts (sourced, not executed).
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
. "$REPO/deploy/versions.env"
. "$REPO/deploy/topology.env"
RUNTIME="${FLHUB_RUNTIME:-$HOME/flhub-runtime}"
HUB_VENV="${HUB_VENV:-$HOME/venvs/hub}"
HUB_UNIT=flhub-hubcore
REGISTRY_NAME=flhub-registry
export PATH="$HOME/.local/bin:$PATH"
ok()   { printf '  %-28s OK   %s\n' "$1" "${2:-}"; }
bad()  { printf '  %-28s FAIL %s\n' "$1" "${2:-}"; }

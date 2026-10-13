#!/usr/bin/env bash
# Stop the hub core. --all also stops the registry.
set -euo pipefail
. "$(dirname "$0")/common.sh"
sudo systemctl stop "$HUB_UNIT" || true
[ "${1:-}" = "--all" ] && docker stop "$REGISTRY_NAME" >/dev/null || true
echo "stopped"

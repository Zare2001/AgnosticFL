#!/usr/bin/env bash
# Start the hub core and the registry (framework servers: deploy/central/<fw>/start.sh).
set -euo pipefail
. "$(dirname "$0")/common.sh"
docker start "$REGISTRY_NAME" >/dev/null
sudo systemctl start "$HUB_UNIT"
for _ in $(seq 1 30); do
  curl -sf "http://127.0.0.1:$HUB_CORE_PORT/health" >/dev/null && { echo "hub core up"; exit 0; }
  sleep 1
done
echo "hub core did not come up; see: journalctl -u $HUB_UNIT" >&2; exit 1

#!/usr/bin/env bash
# Installs this hospital's standard federated-learning client (NVFlare, vantage6 or Flower, see kit.env)
# with the operator's credentials from this kit. Idempotent. Nothing of the hub is installed: the
# training code reaches the client through the framework's own channel when a run starts.
. "$(dirname "$0")/lib.sh"
# shellcheck source=/dev/null
. "$KIT/framework.sh"
log "install: $FRAMEWORK client for site $SITE (operator $CENTRAL_IP, kit $KIT_SOURCE)"
ensure_uv
mkdir -p "$RUN" "$DATA_DIR"
fw_install
log "install: OK"
log "next: put this site's data in $DATA_DIR (demo: ./prepare_data.sh), then ./start.sh"

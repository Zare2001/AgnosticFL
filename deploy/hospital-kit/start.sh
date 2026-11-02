#!/usr/bin/env bash
# Starts this hospital's client and waits until it is connected to the operator's server. Idempotent.
. "$(dirname "$0")/lib.sh"
# shellcheck source=/dev/null
. "$KIT/framework.sh"
mkdir -p "$RUN"
data=$(find "$DATA_DIR" -maxdepth 1 \( -name "$SITE.csv" -o -name "*-$SITE.csv" \) 2>/dev/null | sort | tr '\n' ' ')
[ -n "$data" ] || log "warning: no data files for $SITE in $DATA_DIR yet (<variant>-$SITE.csv); runs will fail until they exist"
fw_start

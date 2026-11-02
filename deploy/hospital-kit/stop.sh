#!/usr/bin/env bash
# Stops this hospital's client. Idempotent.
. "$(dirname "$0")/lib.sh"
# shellcheck source=/dev/null
. "$KIT/framework.sh"
fw_stop

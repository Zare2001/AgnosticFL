#!/usr/bin/env bash
# Remove a test Flower server side (a second SuperLink next to the live one): stop its adapter and
# SuperLink, then delete its runtime dir (state, logs, TLS material and CA key, node map).
# Refuses the live runtime ~/flhub-runtime/flower.
#   FLHUB_FLOWER_RUNTIME=~/flhub-runtime/flower-tls FLHUB_FLOWER_CONTROL_PORT=9193 \
#   FLHUB_ADAPTER_FLOWER_PORT=8823 bash deploy/central/flower/remove_test.sh
set -euo pipefail
. "$(dirname "$0")/common.sh"
live="$(realpath -m "$HOME/flhub-runtime/flower")"
rt="$(realpath -m "$RUNTIME")"
[ "$rt" != "$live" ] || { echo "remove_test: $RUNTIME is the live Flower runtime: refused" >&2; exit 1; }
[ -n "${FLHUB_FLOWER_RUNTIME:-}" ] || { echo "remove_test: set FLHUB_FLOWER_RUNTIME to the test runtime" >&2; exit 1; }
case "$rt" in "$HOME"/flhub-runtime/flower-?*) ;; *) echo "remove_test: $rt is not a ~/flhub-runtime/flower-<name> test runtime: refused" >&2; exit 1 ;; esac
bash "$(dirname "$0")/stop.sh"
rm -rf "$rt"
echo "remove_test: $rt removed"

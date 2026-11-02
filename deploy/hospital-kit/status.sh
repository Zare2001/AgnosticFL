#!/usr/bin/env bash
# Health of this hospital's client: running and connected to the operator's server (exit 0), or not.
. "$(dirname "$0")/lib.sh"
# shellcheck source=/dev/null
. "$KIT/framework.sh"
set +e
fw_status
rc=$?
for f in "$DATA_DIR/$SITE.csv" "$DATA_DIR"/*-"$SITE".csv; do
  [ -f "$f" ] && echo "  data: $f ($(($(wc -l < "$f") - 1)) rows)"
done
[ "$rc" = 0 ] && echo "$SITE: HEALTHY" || echo "$SITE: UNHEALTHY"
exit "$rc"

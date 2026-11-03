#!/usr/bin/env bash
# E5 extra NVFlare hospitals ($NVF_EXTRA_SITES, default nvf-h7 nvf-h8) on this VM:
#   extra.sh install [<site>.signed.zip ...]   kits (default $NVF_INCOMING/nvf-h{7,8}.signed.zip) + data;
#                                    a signed zip answers this site's request.sh
#   extra.sh start | stop | status
# Same scripts and venv as the base sites; data = organamnist-iid12 next to <site>.csv (that file
# itself does not exist for extra sites). stop.sh for these sites leaves the base sites' watchdog on.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/common.sh"
read -ra extra <<<"$NVF_EXTRA_SITES"
case "${1:-status}" in
  install)
    shift
    kits=("$@")
    if [ ${#kits[@]} -eq 0 ]; then
      for s in "${extra[@]}"; do
        [ -f "$NVF_INCOMING/$s.signed.zip" ] && kits+=("$NVF_INCOMING/$s.signed.zip")
      done
    fi
    for kit in "${kits[@]}"; do
      case "$kit" in
        *.signed.zip) nvf_install_signed "$kit" "$NVF_EXTRA_SITES" ;;
        *) echo "extra: $kit is not a <site>.signed.zip" >&2; exit 1 ;;
      esac
    done
    nvf_data materialize --variants organamnist-iid12 --sites "${extra[@]}" --out "$FLHUB_DATA_DIR" \
      --manifests "$REPO/configs/stage2/partitions" | sed 's/^/extra: data /'
    ;;
  start) bash "$HERE/start.sh" "${extra[@]}" ;;
  stop) bash "$HERE/stop.sh" "${extra[@]}" ;;
  status) bash "$HERE/status.sh" "${extra[@]}" ;;
  *) echo "usage: $0 {install [<site>.signed.zip ...]|start|stop|status}" >&2; exit 2 ;;
esac

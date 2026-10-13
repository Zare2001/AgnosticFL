# Shared settings for deploy/nvflare-vm/*.sh (sourced). Hospital side: NVFlare clients only.
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
. "$REPO/deploy/topology.env"
. "$REPO/deploy/versions.env"

NVF_VENV="${NVF_VENV:-$HOME/venvs/nvflare}"
NVF_SITES_DIR="${NVF_SITES_DIR:-$HOME/flhub-runtime/nvflare/sites}"     # one startup kit per hospital
NVF_INCOMING="${NVF_INCOMING:-$HOME/flhub-runtime/nvflare/incoming}"   # kits received from the operator
FLHUB_DATA_DIR="${FLHUB_DATA_DIR:-$HOME/flhub-data}"
NVF_SITES="${NVF_SITES:-$(for s in $SITES; do IFS=: read -r id fw _ <<<"$s"; if [ "$fw" = nvflare ]; then printf '%s ' "$id"; fi; done)}"
NVF_SITES="${NVF_SITES% }"

nvf_pid_alive() { [ -f "$1" ] && kill -0 "$(cat "$1")" 2>/dev/null; }

# Watchdog: cron re-runs start.sh every minute while enabled (start.sh enables, stop.sh disables).
# It covers what the kit's own restart loop cannot: a dead daemon or a VM reboot.
NVF_CRON_TAG="# flhub-nvflare-watchdog"
nvf_watchdog_on() {
  local line="* * * * * bash $REPO/deploy/nvflare-vm/start.sh >> $NVF_SITES_DIR/watchdog.log 2>&1 $NVF_CRON_TAG"
  crontab -l 2>/dev/null | grep -qF "$line" && return 0
  { crontab -l 2>/dev/null | grep -vF "$NVF_CRON_TAG" || true; echo "$line"; } | crontab -
}
nvf_watchdog_off() {
  { crontab -l 2>/dev/null | grep -vF "$NVF_CRON_TAG" || true; } | crontab -
}

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

# Watchdog: one cron line per sites dir runs `start.sh --watchdog` every minute, which starts every
# site of that dir with an enabled marker ($ws/.enabled: start.sh sets it, stop.sh removes it).
# It covers what the kit's own restart loop cannot: a dead daemon, a daemon that gave up, a reboot.
NVF_CRON_TAG="# flhub-nvflare-watchdog $NVF_SITES_DIR"
nvf_enabled_sites() {
  local m
  for m in "$NVF_SITES_DIR"/*/.enabled; do [ -e "$m" ] && basename "$(dirname "$m")"; done
  return 0
}
nvf_watchdog_sync() {  # the cron line exists iff a site of this sites dir is enabled
  local line cur want
  line="* * * * * NVF_SITES_DIR=$NVF_SITES_DIR NVF_VENV=$NVF_VENV FLHUB_DATA_DIR=$FLHUB_DATA_DIR"
  line+=" bash $REPO/deploy/nvflare-vm/start.sh --watchdog >> $NVF_SITES_DIR/watchdog.log 2>&1 $NVF_CRON_TAG"
  cur=$(crontab -l 2>/dev/null || true)
  # other lines: not ending with this dir's tag, and not the stage-1 form (tag without a directory)
  want=$(awk -v tag="$NVF_CRON_TAG" 'NF && substr($0, length($0) - length(tag) + 1) != tag \
    && $0 !~ /# flhub-nvflare-watchdog$/' <<<"$cur")
  if [ -n "$(nvf_enabled_sites)" ]; then want=$(printf '%s\n%s' "$want" "$line" | sed '/^$/d'); fi
  [ "$want" = "$cur" ] || printf '%s\n' "$want" | sed '/^$/d' | crontab -
}

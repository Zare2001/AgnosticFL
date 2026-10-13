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

# Installs one operator kit archive (<site>/startup/...) as $NVF_SITES_DIR/<site>; the site must be one
# of $2 and not running. The archive is deleted afterwards (it holds the site's private key).
nvf_install_kit() {
  local kit=$1 allowed=$2 tmp site target
  tmp=$(mktemp -d)
  tar -xzf "$kit" -C "$tmp"
  site=$(ls "$tmp")
  if [ "$(wc -w <<<"$site")" != 1 ] || [ ! -f "$tmp/$site/startup/fed_client.json" ]; then
    echo "install: $kit is not a single NVFlare client kit" >&2; rm -rf "$tmp"; return 1
  fi
  if ! grep -qw -- "$site" <<<"$allowed"; then
    echo "install: kit for unexpected site '$site' (expected: $allowed)" >&2; rm -rf "$tmp"; return 1
  fi
  if nvf_pid_alive "$NVF_SITES_DIR/$site/daemon_pid.fl"; then
    echo "install: $site is running; stop it first (stop.sh $site)" >&2; rm -rf "$tmp"; return 1
  fi
  mkdir -p "$NVF_SITES_DIR" && chmod 700 "$NVF_SITES_DIR"
  rm -rf "${NVF_SITES_DIR:?}/$site"
  mv "$tmp/$site" "$NVF_SITES_DIR/$site" && rm -rf "$tmp"
  chmod -R go-rwx "$NVF_SITES_DIR/$site"
  rm -f "$kit"
  target=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["servers"][0]["service"]["target"])' \
    "$NVF_SITES_DIR/$site/startup/fed_client.json")
  echo "install: kit $site -> $NVF_SITES_DIR/$site (server $target)"
}

# Data provisioning tool: the repo's flhub_core (not a hospital runtime), with the venv's numpy.
nvf_data() { PYTHONPATH="$REPO/flhub_core/src" FLHUB_DATA_DIR="$FLHUB_DATA_DIR" "$NVF_VENV/bin/python" -m flhub_core.data "$@"; }
NVF_EXTRA_SITES="${NVF_EXTRA_SITES-nvf-h7 nvf-h8}"  # E5 extra sites (deploy/nvflare-vm/extra.sh)

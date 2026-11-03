#!/usr/bin/env bash
# Operator, central machine: ship this checkout's flhub_core (models, data loading, training) to every
# hospital through each framework's own code channel. Nothing happens at the hospitals.
#   bash deploy/central/rollout.sh [--plugins DIR] [nvflare] [vantage6] [flower]   (default: all three)
# --plugins DIR first installs your own models/datasets from DIR/*.py (deploy/central/plugins.sh).
# Changes to the repository's own code: commit first (git am / git pull); the vantage6 image tag is the
# commit, and an uncommitted flhub_core gives a "-dirty" tag. Plugins need no commit: their content is
# part of the image tag. Afterwards every site reports the new code_version, which a run with
# `strict_code_version: true` (configs/hello-pt.yaml) checks.
#   NVFlare   the relay job is exported again with this flhub_core as custom code and resubmitted
#             (the adapter is restarted; the NVFlare server and the clients keep running)
#   vantage6  a new algorithm image, tagged with the commit, is built and pushed to the registry; the
#             adapter is restarted with it; the nodes pull it at their next task
#   Flower    the adapter is restarted: it packs a new FAB and starts a new relay run
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
parts=()
while [ $# -gt 0 ]; do
  case "$1" in
    --plugins) bash "$REPO/deploy/central/plugins.sh" "$2"; shift 2 ;;
    *) parts+=("$1"); shift ;;
  esac
done
[ ${#parts[@]} -gt 0 ] || parts=(nvflare vantage6 flower)
core="$(PYTHONPATH="$REPO/flhub_core/src" python3 -c 'import flhub_core; print(flhub_core.code_version())')"
echo "rollout: flhub_core $core (commit $(git -C "$REPO" rev-parse --short=12 HEAD))"
if [ -n "$(git -C "$REPO" status --porcelain -- flhub_core clients)" ]; then
  echo "rollout: warning: uncommitted changes in flhub_core or clients/" >&2
fi
sudo systemctl restart flhub-hubcore   # the hub venv has flhub_core and the hub as editable installs
for part in "${parts[@]}"; do
  case "$part" in
    nvflare)
      . "$REPO/deploy/central/nvflare/common.sh"
      bash "$REPO/deploy/central/nvflare/install.sh"
      if nvf_pid_alive "$NVF_ADAPTER_PID"; then
        pid=$(cat "$NVF_ADAPTER_PID")
        kill -TERM "$pid"   # the adapter ends the running relay job, then exits
        for _ in $(seq 1 40); do kill -0 "$pid" 2>/dev/null || break; sleep 1; done
        kill -0 "$pid" 2>/dev/null && kill -9 "$pid"
        rm -f "$NVF_ADAPTER_PID"
      fi
      bash "$REPO/deploy/central/nvflare/start.sh" ;;
    vantage6)
      bash "$REPO/deploy/central/vantage6/install.sh"
      bash "$REPO/deploy/central/vantage6/start.sh" ;;
    flower)
      bash "$REPO/deploy/central/flower/restart.sh" adapter ;;
    *) echo "rollout: unknown part $part (nvflare | vantage6 | flower)" >&2; exit 2 ;;
  esac
done
echo "rollout: done; check with: bash deploy/central/status.sh"

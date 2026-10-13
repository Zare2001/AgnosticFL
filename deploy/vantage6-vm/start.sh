#!/usr/bin/env bash
# Start the vantage6 nodes v6-h3 and v6-h4 (standard `v6 node start`) and wait until both are
# connected to the server. Idempotent: running nodes are left alone. Needs install.sh first.
set -euo pipefail
SITES="v6-h3 v6-h4"
VENV="$HOME/venvs/v6"
for s in $SITES; do
  [ -f "$HOME/.config/vantage6/node/$s.yaml" ] || { echo "no node config for $s: run deploy/vantage6-vm/install.sh" >&2; exit 1; }
  if [ -z "$(docker ps -q --filter "name=^vantage6-$s-user$")" ]; then
    (cd "$HOME" && "$VENV/bin/v6" node start -n "$s" --user >/dev/null)
    echo "[v6-vm-start] started node $s"
  fi
done
for s in $SITES; do
  for _ in $(seq 1 90); do
    last="$(docker logs "vantage6-$s-user" 2>&1 | grep -E 'Websocket connection established|Connected to the /tasks|Disconnected from the server' | tail -1 || true)"
    case "$last" in *Disconnected*|"") sleep 1 ;; *) break ;; esac
  done
  case "$last" in
    *Disconnected*|"") echo "[v6-vm-start] node $s not connected; docker logs vantage6-$s-user:" >&2
                       docker logs --tail 15 "vantage6-$s-user" 2>&1 | grep -v -i api_key >&2; exit 1 ;;
    *) echo "[v6-vm-start] node $s connected" ;;
  esac
done

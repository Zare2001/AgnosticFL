#!/usr/bin/env bash
# Health of the vantage6 hospital nodes. Exit 0 = both nodes running and connected to the server.
set -uo pipefail
ok=0
mode="$(cat "$HOME/flhub-runtime/vantage6/node-mode" 2>/dev/null || echo '?')"
for s in v6-h3 v6-h4; do
  cfg="$HOME/.config/vantage6/node/$s.yaml"
  server="$(grep -E '^(server_url|port):' "$cfg" 2>/dev/null | awk '{print $2}' | paste -sd: -)"
  if [ -z "$(docker ps -q --filter "name=^vantage6-$s-user$")" ]; then
    echo "$s  DOWN  container vantage6-$s-user not running ($mode, $server)"; ok=1; continue
  fi
  image="$(docker inspect --format '{{.Config.Image}}' "vantage6-$s-user")"
  last="$(docker logs "vantage6-$s-user" 2>&1 | grep -E 'Websocket connection established|Connected to the /tasks|Disconnected from the server' | tail -1)"
  case "$last" in
    *Disconnected*|"") echo "$s  DISCONNECTED  $image -> $server"; ok=1 ;;
    *) echo "$s  OK    connected  $image -> $server ($mode)" ;;
  esac
  task="$(docker logs "vantage6-$s-user" 2>&1 | grep -E 'New task has been added' | tail -1 | sed 's/.*task_id=/last task_id=/; s/\x1b\[[0-9;]*m//g' | tr -d '\r')"
  [ -n "$task" ] && echo "      $task"
done
exit $ok

#!/usr/bin/env bash
# E6b straggler on the vantage6 VM: CPU-throttle the training of ONE site. vantage6 4.15 nodes
# start algorithm containers without cpu limits and have no config knob for them, so a watcher
# applies `docker update --cpus <cpu%/100>` to every algorithm container of that node
# (labels vantage6-type=algorithm, node=<node>) within 0.2 s of its start, before torch is
# imported. The node itself and the other site are not touched.
# Usage: bash deploy/vantage6-vm/straggler.sh on <site> <cpu%> [--node NAME] | off <site> | status
#   --node NAME  node name if it differs from the site (island: isl-h3 for v6-h3)
# Log: ~/flhub-runtime/vantage6/e6/straggler-<site>.log (UTC time, container, applied limit)
set -euo pipefail
DIR="$HOME/flhub-runtime/vantage6/e6"
mkdir -p "$DIR"
now() { date -u +%Y-%m-%dT%H:%M:%S.%3NZ; }

watch() {  # background worker: watch <site> <node> <cpus>
  local site="$1" node="$2" cpus="$3" seen=" " c
  local logf="$DIR/straggler-$site.log"
  echo "$(now) on site=$site node=$node cpus=$cpus" >> "$logf"
  while true; do
    for c in $(docker ps -q --no-trunc --filter label=vantage6-type=algorithm --filter "label=node=$node"); do
      case "$seen" in *" $c "*) continue ;; esac
      if docker update --cpus "$cpus" "$c" >/dev/null 2>&1; then
        echo "$(now) throttled $(docker inspect --format '{{.Name}}' "$c" 2>/dev/null | tr -d /) to $cpus CPU" >> "$logf"
      fi
      seen="$seen$c "
    done
    sleep 0.2
  done
}

case "${1:-}" in
  on)
    site="${2:?site}"; pct="${3:?cpu%}"; node="$site"
    [ "${4:-}" = "--node" ] && node="${5:?--node NAME}"
    [[ "$pct" =~ ^[0-9]+$ ]] && [ "$pct" -ge 1 ] && [ "$pct" -le 100 ] || { echo "cpu% must be 1..100" >&2; exit 2; }
    cpus="$(awk -v p="$pct" 'BEGIN { printf "%.2f", p / 100 }')"
    if [ -f "$DIR/straggler-$site.pid" ] && kill -0 "$(cat "$DIR/straggler-$site.pid")" 2>/dev/null; then
      echo "straggler already on for $site (off first)" >&2; exit 1
    fi
    setsid nohup bash "$0" _watch "$site" "$node" "$cpus" >/dev/null 2>&1 < /dev/null &
    echo $! > "$DIR/straggler-$site.pid"
    echo "STRAGGLER ON $site: algorithm containers of node $node limited to $cpus CPU ($pct%), log $DIR/straggler-$site.log"
    ;;
  _watch) shift; watch "$@" ;;
  off)
    site="${2:?site}"
    if [ -f "$DIR/straggler-$site.pid" ]; then
      pid="$(cat "$DIR/straggler-$site.pid")"
      kill -- "-$pid" 2>/dev/null || kill "$pid" 2>/dev/null || true
      rm -f "$DIR/straggler-$site.pid"
    fi
    echo "$(now) off" >> "$DIR/straggler-$site.log"
    echo "STRAGGLER OFF $site (running algorithm containers keep their limit until they exit)"
    ;;
  status)
    found=0
    for f in "$DIR"/straggler-*.pid; do
      [ -f "$f" ] || continue
      found=1; s="$(basename "$f" .pid)"; s="${s#straggler-}"
      if kill -0 "$(cat "$f")" 2>/dev/null; then echo "$s ON: $(grep ' on ' "$DIR/straggler-$s.log" | tail -1)"
      else echo "$s stale pid file"; fi
    done
    [ "$found" = 1 ] || echo "no straggler on"
    ;;
  *) echo "usage: $0 on <site> <cpu%> [--node NAME] | off <site> | status" >&2; exit 2 ;;
esac

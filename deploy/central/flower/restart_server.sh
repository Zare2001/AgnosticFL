#!/usr/bin/env bash
# E6e: restart ONLY the Flower framework server on central (the SuperLink), not the adapter.
#   bash deploy/central/flower/restart_server.sh [--kill]     # default SIGTERM; --kill = SIGKILL
# The SuperLink comes back with its state (--database: runs, node ids); the SuperNodes reconnect
# by themselves and the adapter relaunches the relay run it lost. Returns once the server side is
# HEALTHY with a new relay run; prints UTC times: signal, SuperLink up, healed.
exec bash "$(dirname "$0")/restart.sh" superlink "$@"

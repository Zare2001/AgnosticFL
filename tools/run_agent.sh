#!/usr/bin/env bash
# Runs the Claude Code agent for this VM non-stop until the project is DONE.
#
#   tmux new -s flhub 'bash ~/AgnosticFL/tools/run_agent.sh'
#
# - Role comes from ~/.flhub/role (central | nvflare | vantage6 | flower).
# - Orchestrator: Opus 5.5 at max effort. Subagents set their own model/effort in .claude/agents/.
# - One persistent session per VM (id in ~/.flhub/session_id); restarts resume it.
# - Stops when ~/.flhub/DONE or ~/.flhub/STOP exists, or after MAX_HOURS (safety cap).
set -uo pipefail

STATE="$HOME/.flhub"
REPO="${FLHUB_REPO_DIR:-$HOME/AgnosticFL}"
ROLE="$(cat "$STATE/role")"
ROLE_UPPER="$(echo "$ROLE" | tr '[:lower:]' '[:upper:]')"
MODEL="${FLHUB_MODEL:-claude-opus-5-5}"
EFFORT="${FLHUB_EFFORT:-max}"            # max | xhigh
MAX_HOURS="${FLHUB_MAX_HOURS:-96}"       # safety cap; raise if needed
LOGDIR="$STATE/logs"; mkdir -p "$LOGDIR"

set -a; . "$STATE/secrets.env"; set +a
export FLHUB_ROLE="$ROLE"
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
export WANDB_MODE="${WANDB_MODE:-offline}"   # online only for final runs (see COLLABORATION.md)

if [ ! -f "$STATE/session_id" ]; then
  python3 -c 'import uuid; print(uuid.uuid4())' > "$STATE/session_id"
fi
SID="$(cat "$STATE/session_id")"
START=$(date +%s)
cd "$REPO" || { echo "repo $REPO missing; run bootstrap_vm.sh" >&2; exit 1; }

GOAL="/goal You are the ${ROLE_UPPER} agent of the AgnosticFL project on this VM. \
Follow CLAUDE.md and docs/agents/roles/${ROLE_UPPER}.md exactly. The goal is met ONLY when \
every item of the Definition of Done in docs/agents/PROJECT_PLAN.md section 10 is satisfied and \
verified, the central agent has broadcast 'all-done' on the agent bus, and the file ~/.flhub/DONE exists."

RESUME_PROMPT="Continue working toward your goal as the ${ROLE_UPPER} agent. First re-read \
docs/agents/worklogs/${ROLE}.md, run 'git pull --rebase', and check 'tools/bus inbox'. \
Then pick up the next unfinished task in docs/agents/roles/${ROLE_UPPER}.md."

COMMON=(--model "$MODEL" --effort "$EFFORT" --permission-mode bypassPermissions
        --output-format stream-json --verbose)

first=1
[ -f "$STATE/session_started" ] && first=0

while [ ! -f "$STATE/DONE" ] && [ ! -f "$STATE/STOP" ]; do
  if [ $(( ($(date +%s) - START) / 3600 )) -ge "$MAX_HOURS" ]; then
    echo "$(date -Is) MAX_HOURS=$MAX_HOURS reached, stopping" | tee -a "$LOGDIR/runner.log"
    break
  fi
  git -C "$REPO" pull --rebase --quiet || true
  LOG="$LOGDIR/claude-$(date +%Y%m%d-%H%M%S).jsonl"
  echo "$(date -Is) starting claude (first=$first) log=$LOG" | tee -a "$LOGDIR/runner.log"
  if [ "$first" = 1 ]; then
    claude -p "$GOAL" --session-id "$SID" "${COMMON[@]}" > "$LOG" 2>&1
    rc=$?
    # only switch to --resume once the session really exists (e.g. not after an auth failure)
    if grep -q '"session_id"' "$LOG"; then touch "$STATE/session_started"; first=0; fi
  else
    claude -p --resume "$SID" "$RESUME_PROMPT" "${COMMON[@]}" > "$LOG" 2>&1
    rc=$?
  fi
  echo "$(date -Is) claude exited rc=$rc" | tee -a "$LOGDIR/runner.log"
  [ -f "$STATE/DONE" ] && break
  sleep 60
done
echo "$(date -Is) runner finished (DONE=$( [ -f "$STATE/DONE" ] && echo yes || echo no))" | tee -a "$LOGDIR/runner.log"

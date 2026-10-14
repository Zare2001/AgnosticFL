#!/usr/bin/env bash
# Runs the Claude Code agent for this VM non-stop until the current stage is DONE.
#
#   stage 1:  tmux new -s flhub  'bash ~/AgnosticFL/tools/run_agent.sh'
#   stage 2+: tmux new -s flhub2 'bash ~/.flhub/bin/run_agent.sh'   (copy made by start_stage2.sh)
#
# - Role from ~/.flhub/role; stage from ~/.flhub/stage (default 1) or FLHUB_STAGE.
# - Orchestrator: Opus 5.5 at max effort. Subagents set their own model/effort in .claude/agents/.
# - One persistent session per VM and stage; restarts resume it.
#   stage 1: ~/.flhub/{session_id,session_started,DONE,logs/}     (unchanged from stage 1)
#   stage N: ~/.flhub/stageN/{session_id,session_started,DONE,logs/}
# - Stops when the stage's DONE file or ~/.flhub/STOP exists, or after MAX_HOURS (safety cap).
# The body is wrapped in main() so bash parses it completely before running: editing this file
# (e.g. via git pull) while it runs cannot corrupt the running loop.
set -uo pipefail

main() {
  local STATE REPO ROLE ROLE_UPPER STAGE MODEL EFFORT MAX_HOURS SDIR LOGDIR SID START
  STATE="$HOME/.flhub"
  REPO="${FLHUB_REPO_DIR:-$HOME/AgnosticFL}"
  ROLE="$(cat "$STATE/role")"
  ROLE_UPPER="$(echo "$ROLE" | tr '[:lower:]' '[:upper:]')"
  STAGE="${FLHUB_STAGE:-$(cat "$STATE/stage" 2>/dev/null || echo 1)}"
  MODEL="${FLHUB_MODEL:-claude-opus-5-5}"
  EFFORT="${FLHUB_EFFORT:-max}"            # max | xhigh
  MAX_HOURS="${FLHUB_MAX_HOURS:-96}"       # safety cap; raise if needed

  local PLAN DOD ROLEFILE WORKLOG ALLDONE
  if [ "$STAGE" = 1 ]; then
    SDIR="$STATE"
    PLAN="docs/agents/PROJECT_PLAN.md"; DOD="section 10"
    ROLEFILE="docs/agents/roles/${ROLE_UPPER}.md"
    WORKLOG="docs/agents/worklogs/${ROLE}.md"
    ALLDONE="'all-done'"
  else
    SDIR="$STATE/stage$STAGE"
    PLAN="docs/agents/stage$STAGE/PLAN.md"; DOD="section 9"
    ROLEFILE="docs/agents/stage$STAGE/roles/${ROLE_UPPER}.md"
    WORKLOG="docs/agents/worklogs/stage$STAGE-${ROLE}.md"
    ALLDONE="'S$STAGE all-done'"
  fi
  LOGDIR="$SDIR/logs"; mkdir -p "$LOGDIR"

  set -a; . "$STATE/secrets.env"; set +a
  export FLHUB_ROLE="$ROLE" FLHUB_STAGE="$STAGE"
  export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
  export WANDB_MODE="${WANDB_MODE:-offline}"   # final runs: offline -> INV-1 check -> wandb sync

  cd "$REPO" || { echo "repo $REPO missing" >&2; exit 1; }
  [ -f "$PLAN" ] || git pull --rebase --quiet || true
  [ -f "$PLAN" ] || { echo "$PLAN not found in $REPO (stage $STAGE not seeded yet?)" >&2; exit 1; }

  if [ ! -f "$SDIR/session_id" ]; then
    python3 -c 'import uuid; print(uuid.uuid4())' > "$SDIR/session_id"
  fi
  SID="$(cat "$SDIR/session_id")"
  START=$(date +%s)

  local GOAL RESUME_PROMPT
  GOAL="/goal You are the ${ROLE_UPPER} agent of the AgnosticFL project on this VM, stage ${STAGE}. \
Follow CLAUDE.md and ${ROLEFILE} exactly. The goal is met ONLY when every item of the Definition \
of Done in ${PLAN} ${DOD} is satisfied and verified, the central agent has broadcast ${ALLDONE} on \
the agent bus, and the file ${SDIR}/DONE exists."

  RESUME_PROMPT="Continue working toward your goal as the ${ROLE_UPPER} agent (stage ${STAGE}). \
First re-read ${WORKLOG}, run 'git pull --rebase', and check 'tools/bus inbox'. If you hold a \
RUNLOCK or an active netem rule, resolve that first. Then pick up the next unfinished task in ${ROLEFILE}."

  # Stage 1 ran with bypassPermissions. From stage 2 on, agents run in auto mode: a classifier
  # reviews every action, blocks risky ones, and the agent keeps working on other tasks.
  # Trusted infrastructure + hard deny rules come from $SDIR/claude-settings.json (start_stage2.sh).
  local COMMON=(--model "$MODEL" --effort "$EFFORT" --output-format stream-json --verbose)
  if [ "$STAGE" = 1 ]; then
    COMMON+=(--permission-mode bypassPermissions)
  else
    [ -f "$SDIR/claude-settings.json" ] || { echo "$SDIR/claude-settings.json missing; run start_stage2.sh" >&2; exit 1; }
    COMMON+=(--permission-mode auto --settings "$SDIR/claude-settings.json")
  fi

  local first=1 LOG rc
  [ -f "$SDIR/session_started" ] && first=0

  while [ ! -f "$SDIR/DONE" ] && [ ! -f "$STATE/STOP" ]; do
    if [ $(( ($(date +%s) - START) / 3600 )) -ge "$MAX_HOURS" ]; then
      echo "$(date -Is) MAX_HOURS=$MAX_HOURS reached, stopping" | tee -a "$LOGDIR/runner.log"
      break
    fi
    git -C "$REPO" pull --rebase --quiet || true
    LOG="$LOGDIR/claude-$(date +%Y%m%d-%H%M%S).jsonl"
    echo "$(date -Is) stage $STAGE: starting claude (first=$first) log=$LOG" | tee -a "$LOGDIR/runner.log"
    if [ "$first" = 1 ]; then
      claude -p "$GOAL" --session-id "$SID" "${COMMON[@]}" > "$LOG" 2>&1
      rc=$?
      # only switch to --resume once the session really exists (e.g. not after an auth failure)
      if grep -q '"session_id"' "$LOG"; then touch "$SDIR/session_started"; first=0; fi
    else
      claude -p --resume "$SID" "$RESUME_PROMPT" "${COMMON[@]}" > "$LOG" 2>&1
      rc=$?
    fi
    echo "$(date -Is) claude exited rc=$rc" | tee -a "$LOGDIR/runner.log"
    [ -f "$SDIR/DONE" ] && break
    if [ "$STAGE" != 1 ]; then
      local pm
      pm="$(jq -r 'select(.type=="system" and .subtype=="init") | .permissionMode // empty' "$LOG" 2>/dev/null | head -1)"
      if [ -n "$pm" ] && [ "$pm" != auto ]; then
        echo "$(date -Is) STOPPING: session ran in permission mode '$pm', not 'auto' (auto mode unavailable?). See README_STAGE2.md." \
          | tee -a "$LOGDIR/runner.log"
        break
      fi
    fi
    sleep 60
  done
  echo "$(date -Is) stage $STAGE runner finished (DONE=$( [ -f "$SDIR/DONE" ] && echo yes || echo no))" \
    | tee -a "$LOGDIR/runner.log"
}

main "$@"
exit $?

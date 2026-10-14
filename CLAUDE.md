# AgnosticFL — agent instructions (all VMs)

## Current stage: 3 — library restructure and documentation

Stage 2 is finished: see `REPORT_STAGE2.md` and tag `stage2-final`. The stage-2 version of this file is archived at `docs/agents/stage2/CLAUDE.stage2.md`.

Stage 3 runs **no experiments** and changes **no system code**. It turns `main` into a clean, documented library repository; all agent material stays on branch `archive/agentic-build`.

| Role | VM | Owns in stage 3 |
|---|---|---|
| `central` (lead) | 145.38.205.58 | restructure of `main`, README, all shared docs, results (incl. new stage-1 plots), cost-and-efficiency, GitHub files, merge and release |
| `nvflare` | 145.38.189.142 | `docs/frameworks/nvflare.md`, `adapters/nvflare/README.md`, `clients/nvflare/README.md`, review of every NVFlare statement |
| `vantage6` | 145.38.186.63 | `docs/frameworks/vantage6.md`, `adapters/vantage6/README.md`, `clients/vantage6/README.md`, review of every vantage6 statement |
| `flower` | 145.38.187.20 | `docs/frameworks/flower.md`, `adapters/flower/README.md`, `clients/flower/README.md`, review of every Flower statement, quick-start verification |

Your role is in `~/.flhub/role`. Your stage-3 role file is `docs/agents/stage3/roles/<ROLE>.md` (uppercase). Read it now.

@docs/agents/stage3/PLAN.md
@docs/agents/COLLABORATION.md

`COLLABORATION.md` still defines the bus, announcements and git style. Where it says "main", stage 3 uses the branch from PLAN.md §2 instead.

## Two checkouts — know where you are

| Directory | Branch | What you commit there |
|---|---|---|
| `~/AgnosticFL` | `archive/agentic-build` | Only agent bookkeeping: your worklog `docs/agents/worklogs/stage3-<role>.md`, stage-3 notes, the bus transcript. This is also where `tools/bus` lives and where the running services come from. |
| `~/AgnosticFL-main` | `restructure/library` | The library and its documentation (the future `main`). Never agent material. |

Before every commit: `git -C <dir> branch --show-current` and check that it is the right branch for the files you commit.

## Non-negotiable rules

1. **Never stop working until stage 3 is DONE** (PLAN.md §9). When blocked, say so on the bus, then do useful parallel work or `tools/bus wait --timeout 600`.
2. **Git as Zare2001 only.** No Co-Authored-By, no "Generated with", no session links, no emoji. Messages `<scope>: <what>`, ≤ 72 characters. Commits ≤ 400 changed lines, pushes ≤ 8 commits, except the two owner-approved cases in PLAN.md §2 rule 6. Hooks enforce this. Never use `--no-verify`.
3. **After every push, announce it** on the bus with the branch name. Pull when someone announces `pull: yes`.
4. **No code changes** beyond PLAN.md §3. If a document would need a code change to be true, change the document, or list the gap in `limitations-and-future-work.md`.
5. **The testbed is read-only** (PLAN.md §2 rule 3): no stop/start/restart/install/purge, no runs, no `tc`/netem, no injections, no W&B writes.
6. **Never push to `main` before R4, never force-push, never delete a branch or tag.**
7. **Secrets never leave `~/.flhub/secrets.env`.** Never print them; never put them in a document.
8. **Facts only.** Every number in a document comes from evidence in the repo (stage reports, `runs.md`, evidence files, ledger) or from a command you ran; cite the source file. If a number is missing, write `TODO(verify)` and resolve it before R3.
9. **Use subagents:** `scout` (lookups), `runner` (commands/tests), `gitops` (commit/push/announce) and `scribe` (worklog). Write the documentation yourself.
10. **Keep your worklog** `docs/agents/worklogs/stage3-<role>.md` (in `~/AgnosticFL`) current after every milestone.
11. **You run in auto mode.** Hard deny rules come from `~/.flhub/stage3/claude-settings.json`. If an action is blocked: never reach the same effect another way, never edit the settings or the runner, send `tools/bus send --to human --type blocker` with the exact command and why the plan needs it, and continue with other work.

## On (re)start, always

1. `cat ~/.flhub/role` → read your stage-3 role file.
2. `git -C ~/AgnosticFL pull --rebase` and `git -C ~/AgnosticFL-main pull --rebase`; read your stage-3 worklog tail; `tools/bus inbox`; `tools/bus status`.
3. Continue with the next unfinished task. Update `tools/bus status-set --phase R<n>` when your phase changes.

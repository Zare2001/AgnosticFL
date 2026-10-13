# AgnosticFL — agent instructions (all VMs)

## Current stage: 2 — full system and performance test

Stage 1 (build + IID FedAvg proof) is finished: see `REPORT.md` and git tag `stage1-final`. The stage-1 version of this file is archived at `docs/agents/stage1/CLAUDE.stage1.md`. Notes the agents added there still apply unless stage-2 documents contradict them.

You are one of four autonomous agents, one per VM:

| Role | VM | Owns in stage 2 |
|---|---|---|
| `central` (lead) | 145.38.205.58 | hub (aggregators, flhub_core 0.2, orchestrator, sim), configs, campaign schedule (RUNLOCK), W&B `AgnosticFL-stage2`, `REPORT_STAGE2.md` |
| `nvflare` | 145.38.189.142 | NVFlare adapter + hospitals, NVFlare rollout, payload limits, fault injection, E7 native baseline |
| `vantage6` | 145.38.186.63 | vantage6 adapter + hospitals, vantage6 rollout, payload limits, fault injection, E7 native baseline |
| `flower` | 145.38.187.20 | Flower adapter + hospitals, Flower rollout, payload limits, fault injection, E7 native baseline |

Your role is in `~/.flhub/role`. Your stage-2 role file is `docs/agents/stage2/roles/<ROLE>.md` (uppercase). Read it now. The documents imported below are the plan, the experiment matrix, the protocol and the collaboration rules.

@docs/agents/stage2/PLAN.md
@docs/agents/stage2/EXPERIMENTS.md
@docs/agents/PROTOCOL.md
@docs/agents/COLLABORATION.md

`docs/agents/PROJECT_PLAN.md` (stage 1) still defines topology, architecture, ownership and technical decisions (§2–§5, §11). Read those sections when you need them; its phases and Definition of Done are finished.

## Non-negotiable rules

1. **Never stop working until stage 2 is DONE** (`docs/agents/stage2/PLAN.md` §9). When blocked, say so on the bus, then do useful parallel work from your role file or `tools/bus wait --timeout 600`. Do not invent busywork.
2. **Git as Zare2001 only.**
   - No Co-Authored-By, no "Generated with", no session links, no emoji.
   - Messages: `<scope>: <what>`, ≤ 72 characters.
   - Commits ≤ 400 changed lines; pushes ≤ 8 commits.
   - Hooks enforce this. Never use `--no-verify`.
3. **After every push, announce it** on the bus (COLLABORATION.md §4). Pull when someone announces `pull: yes` for files you use.
4. **Secrets never leave `~/.flhub/secrets.env`.** Credentials travel only via `tools/bus put --secret`.
5. **W&B:**
   - Final stage-2 runs only, project `AgnosticFL-stage2`.
   - Central runs every final run. `scripts/stage2/finalize_run.py` uploads it only after its gate passes (PLAN.md §6).
   - Never log to or delete from project `AgnosticFL` (stage 1).
6. **Hospitals stay standard.** Hospital-side code is the framework's normal client pattern. Every hospital-side change (code, config, data, ops) gets a row in `docs/report/stage2/ledger.md`.
7. **Safety:** network shaping and fault injection only under PLAN.md §8: framework ports only, revert timer first, announce on the bus, never touch SSH/7700/firewall/reboot.
8. **One final run at a time, all on central.** Framework agents ask for a RUNLOCK before disturbing the system, and arm E6 injections only on request (ARM → ARMED; PLAN.md §6).
9. **Use subagents:** `scout` (lookups), `runner` (commands/tests/logs), `gitops` (commit/push/announce) and `scribe` (worklogs/docs) run on Haiku. Do design, numerics, debugging and integration yourself.
10. **Keep your worklog** `docs/agents/worklogs/stage2-<role>.md` current after every milestone. It is how you recover after a restart or context compaction.
11. **Verify, don't assume:** check framework APIs and limits in the installed version's source (`scout`).
12. **Disk:** keep ≥ 3 GB free (PLAN.md §7).
13. **You run in auto mode.** A safety classifier reviews every action; hard deny rules come from `~/.flhub/stage2/claude-settings.json`. If an action is blocked:
    - Never try to reach the same effect another way: no rewording, no scripts, no other tools.
    - Never edit the settings file or the runner to loosen it.
    - Send `tools/bus send --to human --type blocker` with the exact command, the block reason, and why the plan needs it. Put it in your status `--blockers` too.
    - Continue with other work. The owner decides: they run the step themselves or adjust the settings.

## On (re)start, always

1. `cat ~/.flhub/role` → read your stage-2 role file.
2. `git pull --rebase`, read your stage-2 worklog tail, `tools/bus inbox`, `tools/bus status`.
3. If you hold a RUNLOCK or an active netem rule from before the restart, resolve it first: finish or abort the run, clear the rule, tell the bus.
4. Continue with the next unfinished task. Update `tools/bus status-set --phase S<n>` when your phase changes.

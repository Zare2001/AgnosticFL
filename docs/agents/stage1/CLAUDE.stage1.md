# AgnosticFL — agent instructions (all VMs)

You are one of four autonomous build agents. Each runs on its own VM:

| Role | VM | Owns |
|---|---|---|
| `central` (lead / integrator) | 145.38.205.58 | hub core, orchestrator, protocol, central deployment, W&B, final report |
| `nvflare` | 145.38.189.142 | NVFlare adapter + NVFlare hospital clients |
| `vantage6` | 145.38.186.63 | vantage6 adapter + vantage6 hospital nodes |
| `flower` | 145.38.187.20 | Flower adapter + Flower hospital SuperNodes |

Your role is in `~/.flhub/role`. Your role file is `docs/agents/roles/<ROLE>.md` (uppercase). Read it now, then read the three shared documents imported below.

@docs/agents/PROJECT_PLAN.md
@docs/agents/PROTOCOL.md
@docs/agents/COLLABORATION.md

## Non-negotiable rules (summary — details in COLLABORATION.md)

1. **Never stop working until the project is DONE** (PROJECT_PLAN.md §10). If you are blocked, say so on the bus, then work on something else that is useful, or `tools/bus wait` for the answer. Never end your turn just because a step is finished.
2. **Git as Zare2001 only.** Identity is preconfigured. No Co-Authored-By, no "Generated with", no session links, no emoji in commits. Short messages (`<scope>: <what>`, ≤ 72 chars). Small commits (≤ 400 changed lines) and small pushes (≤ 8 commits). Hooks in `.githooks/` enforce this; never bypass them (`--no-verify` is forbidden).
3. **After every push, announce it**: `tools/bus send --to all --type push --subject "<sha> <subject>" --body "paths: ...; pull: yes|optional"`. Pull (`git pull --rebase`) whenever someone announces a push that touches files you use.
4. **Secrets never leave `~/.flhub/secrets.env`**: never print, commit, log, or send them over the bus in plain text. Startup kits and API keys go only via `tools/bus put --secret`.
5. **W&B**: development runs are offline (`WANDB_MODE=offline`, the default in the runner). Only the central agent logs the final runs online, and the project must end with only the final, correct runs (COLLABORATION.md §6).
6. **Use subagents**: `scout` (lookups), `runner` (commands/tests/logs), `gitops` (commit/push/announce), `scribe` (worklogs/docs) all run on Haiku. Do design, debugging and integration yourself.
7. **Keep your worklog** `docs/agents/worklogs/<role>.md` current after every milestone. It is how you recover after a restart or context compaction, and it feeds the final report.
8. **Verify, don't assume**: framework APIs change between versions. Check the installed version's source/docs (`scout`) before using an API.
9. **Hospitals stay standard**: code on the framework VMs must be the framework's normal client pattern. Anything hub-specific lives on the central VM (adapters, relays, orchestrator).

## On (re)start, always

1. `cat ~/.flhub/role` → read your role file.
2. `git pull --rebase`, read your worklog tail, `tools/bus inbox`, `tools/bus status`.
3. Continue with the next unfinished task. Update `tools/bus status-set` when your phase changes.

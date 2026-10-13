# Collaboration rules

## 1. Agent bus (port 7700 on the central VM)

The only real-time channel between agents. `tools/bus` loads your role and secrets automatically.

| Command | Use |
|---|---|
| `tools/bus inbox` | new messages for you (advances your cursor). Check after every task and at least every 15 minutes of work. |
| `tools/bus wait --timeout 600` | block until a message arrives. Use when you are waiting on someone instead of polling in a loop. |
| `tools/bus send --to <agent\|all\|human> --type <type> --subject "…" [--body "…"]` | send |
| `tools/bus status-set --phase P1 --summary "…" [--blockers "…"]` / `tools/bus status` | shared status board |
| `tools/bus put FILE --to <agent> --secret` / `tools/bus get <id> -o PATH` | file transfer; `--secret` encrypts client-side |
| `tools/bus log --since N` / `tools/bus export -o FILE` | history / transcript for the report |

Message types: `push`, `request`, `reply`, `deploy-request`, `deploy-result`, `blocker`, `milestone`, `info`, `secret`, `final-call`, `report-ready`, `all-done`.

Rules:
- Subjects are one line and specific: `"M1-flower island conformance passes"`, not `"update"`.
- Bodies: facts, commands, SHAs, paths, log excerpts (≤ 30 lines). No secrets.
- Answer every `request` addressed to you within your next task boundary, even if only "ack, ETA …".
- Messages to `human` are for things only the project owner can fix (closed firewall port, missing token permission, billing). Also put them in your status `--blockers`.
- If the bus is unreachable: retry for 10 minutes, then use GitHub as fallback: append to `docs/agents/bus_fallback.md` in a small commit and push.

### Deploy requests (framework agent → central)
```
--type deploy-request --subject "deploy nvflare adapter <sha>"
--body "sha: <sha>
run: bash deploy/central/nvflare/install.sh && bash deploy/central/nvflare/start.sh
check: bash deploy/central/nvflare/status.sh  (expect: server up, adapter :8811 healthy)
secrets to send back: startup kits for nvf-h1, nvf-h2 (tools/bus put --secret)"
```
Central answers with `deploy-result` containing PASS/FAIL, the status output and the file ids of any secrets sent.

## 2. Ownership

Directory owners are listed in PROJECT_PLAN.md §4. Edit only what you own. For anything else: `request` to the owner. Shared files (`tests/`, `docs/DECISIONS.md`, `README.md`): small, additive edits only, announced with a `push` message.

## 3. Git rules

- Identity is preconfigured as **Zare2001**. Never change git config. Never add Co-Authored-By, "Generated with …", session URLs, or emoji. The commit-msg hook strips such lines; the pre-push hook blocks pushes that still contain them or have another author.
- Message format: `<scope>: <imperative summary>` — ≤ 72 chars (aim for ≤ 60). Optional body ≤ 2 short lines. Scopes: `core hub orch proto nvflare v6 flower deploy data tests docs report tools`.
  Good: `v6: add train method to algorithm`, `orch: weight FedAvg by num_examples`. Bad: `Update files`, `Implemented the complete adapter for vantage6 including tests and docs`.
- Commit size: one logical change, ≤ 400 changed lines (lockfiles excluded). Split larger work.
- Push size: ≤ 8 commits per push. Push as soon as a coherent step works (tests pass for what you touched). Never push broken `main` knowingly; if you do, fix or revert within 15 minutes.
- Always `git pull --rebase` before pushing. Never force-push. Never rewrite pushed history. Never use `--no-verify`.
- Conflicts: resolve only in files you own; for others' files, `git rebase --abort` and send a `request` to the owner.
- Never commit: secrets, `.env`, startup kits, API keys, certificates, datasets, model weights, W&B dirs, logs. `.gitignore` and the pre-push hook help; you are still responsible.
- Branches: work on the default branch (`main`; trunk-based, small commits). Use a short-lived branch only for risky experiments; merge with a rebase, not a merge commit.

## 4. Push announcements

After **every** push:
```
tools/bus send --to all --type push --subject "<short-sha> <commit subject>" \
  --body "paths: <top-level paths>; pull: yes|optional; note: <one line>"
```
`pull: yes` when others must pull before continuing (shared library, protocol, configs). On receiving `pull: yes` that touches files you use: `git pull --rebase` before your next commit.

## 5. Models and subagents

- You (the orchestrator of this VM) run on Opus 5.5 at max effort. Do the thinking: architecture, debugging, integration, reviews of subagent output.
- Delegate mechanical work to the Haiku subagents in `.claude/agents/`:
  - `scout`: doc/code/log lookups (read-only).
  - `runner`: run commands, tests, installs, deploy scripts; returns PASS/FAIL + key lines.
  - `gitops`: commit/rebase/push/announce under §3–§4.
  - `scribe`: worklog entries, ADRs, report sections from facts you give it.
- Run independent subagent tasks in parallel. Always check their results before acting on them. Never delegate decisions about protocol, FedAvg, or the final runs.

## 6. Weights & Biases

- Key in `~/.flhub/secrets.env`; never print it. The runner sets `WANDB_MODE=offline` by default.
- **Development and island runs: offline only** (`WANDB_MODE=offline`). Delete local `wandb/` dirs when no longer useful.
- **Final runs: central orchestrator only**, `WANDB_MODE=online`, project `$WANDB_PROJECT`, tag `final`, group `final-<date>`, names as in PROJECT_PLAN.md §6 P3.
- If a final run fails or is superseded, delete it from W&B immediately (`scripts/wandb_cleanup.py`).
- Before `all-done`, `scripts/wandb_cleanup.py --apply` must leave only finished runs tagged `final`; list them (name, id, URL, final metrics) in the report.

## 7. Secrets

- Live only in `~/.flhub/secrets.env` (mode 600) and in runtime dirs under `~/flhub-runtime/` (never inside the repo).
- Transfer credentials (NVFlare startup kits, vantage6 API keys) only with `tools/bus put --secret`. Delete the downloaded archive after installing it.
- Never echo secrets into logs, the bus, W&B config, commits or the report. Redact as `***`.

## 8. Worklogs and decisions

- Worklog `docs/agents/worklogs/<role>.md`: an entry per milestone and per significant problem solved (what, result, evidence, next). Commit it with your normal work.
- `docs/DECISIONS.md`: one ADR per decision that deviates from or fills a gap in the plan (dataset, versions, workarounds, tolerances).

## 9. Never stop

- The project is finished only at PROJECT_PLAN.md §10. Until then, always have a next action.
- Blocked on another agent: send/answer on the bus, then do useful parallel work (tests, docs, scripts, hardening), or `tools/bus wait`.
- Blocked on the owner (`human`): report once, keep working on everything else, re-check every hour.
- Something keeps failing: change approach after 3 failed attempts, record what you tried in the worklog, use the fallbacks in PROJECT_PLAN.md §11.
- Before ending any turn, ask yourself: is the Definition of Done met? If not, continue.

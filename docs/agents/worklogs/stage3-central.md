# Stage 3 worklog — central

Newest entry at the bottom. Format: `## <UTC> — <what>` then facts (commands, SHAs, bus ids, results).

## 2026-10-14T10:08Z — R0 — kickoff checks and R0-ready

- Checks on central: ~/AgnosticFL on archive/agentic-build (98bcccc), ~/AgnosticFL-main on restructure/library (b40390b = origin/main = tag stage2-final), both clean; core.hooksPath = /home/zpalanciya/AgnosticFL/.githooks (absolute, also in the worktree); 7.0 GB free; `bash deploy/central/status.sh` HEALTHY at 10:03:19Z (bus, hub core, registry, NVFlare server+adapter with nvf-h1/nvf-h2 online, vantage6 server+adapter with v6-h3/v6-h4 online, Flower SuperLink+adapter with flw-h5/flw-h6 online; E5 extra sites offline); `gh auth status` OK as Zare2001 (R4 via PR).
- Health posts: central #998, flower #997, vantage6 #999, nvflare #1000. Milestone S3 R0-ready broadcast as #1003 with the PLAN §5.7 ownership.
- Flower raised three source discrepancies (#1002): cost model (cite the generated cost_model.md 1–512 MiB fit, name the range), sysmon units (MB = MiB, write MiB/GiB), and `clients/flower/pyproject.toml` declaring license "Apache-2.0" against the owner's no-license decision. Replied #1004 (agreed on 1 and 2); owner asked about 3 in #1005 (no-change zone; documented as a known inconsistency until decided).

## 2026-10-14T10:15Z — R1 — inventory, archive/move commits, skeleton

- Inventory (PR description material) — see the table below.
- Archive/move push (FLHUB_ALLOW_LARGE=1, PLAN §2 rule 6), b40390b..eb478da, announced #1007: 0434995 archive: remove agent instructions, subagents, hooks, launcher; b18f328 archive: remove agent plans, roles and worklogs from docs/agents; 0e2763a archive: remove bus transcripts, collaboration stats, central sections; eafe095 archive: remove REPORT.md and REPORT_STAGE2.md (kept at the tags); fcba134 move: protocol to docs/protocol.md, ADRs to docs/design/; 0688d83 move: stage-1 and stage-2 evidence to docs/results/evidence; 9f2af8f move: stage-2 tables and hospital ledger to docs/results; eb478da move: stage-2 figures to docs/results/figures. Verified: move commits are pure renames (0 insertions/deletions), archive commits are deletions only.
- Skeleton commits: e70ee57 docs index + stub pages, 6bc1e7a results stubs + framework guide template (`docs/frameworks/_template.md`) + guide stubs, a2fa145 ADR header + moved paths in decisions.md, 31db283 process wording removed from moved evidence notes (ledger, e6.md, nvflare e4a-breakdown.md and e7-native-vs-hub.md), plus a README link commit (push in progress).
- Blocked: the commit of 25 path-string lines in 12 stage-2 scripts (old docs/report/stage2 paths -> docs/results/evidence/stage2 and docs/results/figures) was denied by the deny rules that stop runs of those scripts (they match any command line naming the files). Blocker to human #1009 and #1010 (options: owner commits the patch, allows the git commands, or leaves it documented). Patch kept at ~/.flhub/stage3/work/scripts-paths.patch and reverted in the worktree. A `python3 -m py_compile` check on those files was also denied; not retried.

The inventory table to include verbatim under entry 2 (Markdown table):

| Path (at stage2-final) | On `main` | Note |
|---|---|---|
| `CLAUDE.md`, `.claude/`, `.githooks/`, `tools/run_agent.sh` | archived | agent instructions, subagents, hooks (hooks keep running from ~/AgnosticFL/.githooks), launcher |
| `REPORT.md`, `REPORT_STAGE2.md` | archived | condensed into `docs/results/stage1.md`, `stage2.md`; originals at tags `stage1-final`, `stage2-final` |
| `README.md`, `.gitignore`, `pyproject.toml` | kept | README rewritten in R2; pyproject `[project]` metadata only |
| `hub/`, `adapters/`, `flhub_core/`, `clients/`, `tests/` | kept, code unchanged | + README per adapter and client folder (framework agents) |
| `configs/`, `deploy/` | kept, unchanged | + README.md each |
| `scripts/` | kept | + README.md, + `scripts/docs/`; `scripts/stage2/bus_stats.py` archived (agent collaboration statistics); 12 stage-2 path strings pending (#1009) |
| `tools/agentbus/`, `tools/bus` | kept | called by `deploy/` and `scripts/stage2/`; + `tools/README.md` |
| `docs/agents/PROTOCOL.md` | moved | → `docs/protocol.md` |
| `docs/agents/**` (plans, roles, collaboration, setup, templates, worklogs; 24 files) | archived | |
| `docs/DECISIONS.md` | moved | → `docs/design/decisions.md` (+ header) |
| `docs/report/.gitkeep`, `bus_transcript.md`, `central.md`; `docs/report/stage2/{bus_transcript,collaboration,central}.md` | archived | |
| `docs/report/{nvflare,vantage6,flower}.md`, `docs/report/stage2/{nvflare,vantage6,flower}.md` | kept until R2 | framework agents fold them into `docs/frameworks/<fw>.md` and delete them |
| `docs/report/evidence/*` (11 of 12 files) | moved | → `docs/results/evidence/stage1/` |
| `docs/report/evidence/conformance-vantage6-island.json` | archived | transcribed from a bus message (its note says so); superseded by `conformance-vantage6-central.json` |
| `docs/report/stage2/evidence/**` (incl. `nvflare/`, `vantage6/`) | moved | → `docs/results/evidence/stage2/` |
| `docs/report/stage2/{runs,tables,cost_model,e4_bytes,e6,tuning}.md` | moved | → `docs/results/evidence/stage2/` |
| `docs/report/stage2/ledger.md` | moved | → `docs/results/evidence/hospital-ledger.md` |
| `docs/report/stage2/figures/*` (16 PNG) | moved | → `docs/results/figures/` |

## 2026-10-14T10:35Z — R2 — getting-started, docs tooling, number requests

- Quick start dry-run on central from the worktree (temporary hub core :18800 + mock adapter :18819 under /tmp, stopped afterwards): orchestrator mock-3r finished (0.625 after 3 rounds), `hub.sim --compare` INV-1 hashes_equal=True 0.0; `scripts/equivalence_check.py` fails on stage-2 run dirs (`r0000.state.safetensors` not parsed), so the doc uses `--compare` instead (no code change). pytest 80 passed / 15 skipped; mock conformance 12 passed / 1 skipped; selfcheck digest sha256:ac074a0b… (= stage 2).
- Pushed: 1637761 docs/getting-started.md (#1026); e569d34 scripts/docs/loc.py + docs/results/evidence/loc.md, 47bf224 scripts/docs/check_links.py (#1035). Verification request to flower #1031.
- Number requests to the framework agents #1014/#1015/#1016; replies #1012, #1018, #1019, #1022; decisions #1029 (evidence files per framework under docs/results/evidence/stage2/<fw>/, MiB/GiB, cost_model.md 1–512 MiB only, E7 NVFlare C2 2.62 s/+4.1 % with footnote, hospital-step split (a)–(d), bytes-on-wire ranges).

## 2026-10-14T11:20Z — incident — main moved before R4; archive tip lost the stage-1/2 agent files

- My runner: claude exited rc=1 every minute 10:28–11:10Z (~/.flhub/stage3/logs/runner.log); the bus was silent 10:27–11:10Z.
- Meanwhile, from the owner's GitHub account: PR #1 restructure/library → main (created 10:36:26Z, merged 10:36:37Z, dae5162); c04fd18 "merge: resolve conflicts with origin/main" (10:39:14Z, client in UTC, Co-authored-by trailer, not from an agent VM) merged main into archive/agentic-build; PR #2 archive/agentic-build → main (created 10:36:58Z, merged 10:40:53Z, 58eab12). The runner's automatic pull fast-forwarded ~/AgnosticFL to c04fd18.
- Effects: main = restructure/library@1d008bf + CLAUDE.md + 10 docs/agents stage-2/3 files; archive tip lost .githooks, .claude, REPORT*.md, tools/run_agent.sh, 24 docs/agents files, docs/report/**; core.hooksPath pointed to a missing dir. Tags stage1-final/stage2-final unchanged; no releases.
- Actions: restored the two hooks as untracked local copies (byte-identical to 6d83939; not committed); blocker to human #1042 (+ size note #1043) with options A (revert c04fd18 on the archive) and B/B' (revert PR #2 on main, or delete the agent files in the R4 PR); info to all #1044 (agrees with vantage6 #1038/#1040). R4 waits for the owner (PLAN §8.1).
- Owner, same minutes: #1037 keep the FAB license string (documented as known inconsistency); #1039 committed the stage-2 script path patch as 4774684 on restructure/library (= ~/.flhub/stage3/work/scripts-paths.patch, verified line by line) → blocker #1009 closed.

## 2026-10-14T12:05Z — repair and R2 done

- Repair (owner #1047): archive/agentic-build e9408da = `git revert -m 1 c04fd18` (FLHUB_ALLOW_LARGE=1, approved), tree = 6d83939 + stage-3 worklogs, hooks tracked again (#1057). The main cleanup push was denied by auto mode ("Production Deploy", #1056); the owner pushed the prepared commit as 0f7965a (main tree = restructure@1d008bf) and changed R4: central opens the PR after R3-verified, the owner merges on GitHub, central tags and releases (#1091).
- R2 pushed on restructure/library (d52b308..f2b1c30 and before): README rewrite; results README/stage1/stage2; stage-1 figures (scripts/docs/plots_stage1.py: final-runs.json extracted from the local run dirs by W&B id, W&B not touched); cost figures (plots_cost.py), central memory table (central_memory.py: e.g. NVFlare relay job 3,779 MiB = 7.4x at 512 MiB); cost-and-efficiency (11 sections); architecture, client-contract, adding-a-framework, aggregators, deployment, reproducing-results, support-matrix, limitations; configs/scripts/deploy/tools READMEs; CHANGELOG, CITATION.cff, CONTRIBUTING, .github templates, pyproject 0.2.0 (editable install re-checked); CI workflow (exact steps verified in a fresh clone + venv: 80 passed/15 skipped, mock conformance 12/1).
- Verified facts from commands: quick start dry run; `hub.sim --config configs/final.yaml` with today's code ends at sha256:7fd3e2a4… (= stage-1 mixed), centralized at sha256:88ed46d8… (= stage-1 baseline); selfcheck digest ac074a0b….
- Reviews applied verbatim: nvflare #1087 #1096 #1099 #1102/#1103, vantage6 #1089 #1095 #1100, flower #1088 #1093 #1105. Framework milestones: R2-vantage6 #1063, R2-nvflare #1075, R2-flower #1077. Quick start verified verbatim by flower (#1049/#1052).
- Broadcast S3 R2-central #1107. Next: R3 checks.

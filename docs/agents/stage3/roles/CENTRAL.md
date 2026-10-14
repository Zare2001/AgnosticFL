# Stage 3 role: CENTRAL (lead)

You restructure `main` and write the shared documentation. Work in `~/AgnosticFL-main` (branch `restructure/library`); keep your worklog in `~/AgnosticFL` (branch `archive/agentic-build`). Read PLAN.md fully first.

## R0 Kickoff

- [ ] Create `docs/agents/worklogs/stage3-central.md` (archive branch) if missing.
- [ ] Check both checkouts (branch, clean tree, `git config core.hooksPath` = `~/AgnosticFL/.githooks` absolute), ≥ 3 GB free, `bash deploy/central/status.sh` HEALTHY (read-only).
- [ ] Wait for the three health posts, then broadcast **S3 R0-ready** with the planned file ownership (PLAN.md §5.7) and your R1 ETA.

## R1 Skeleton (on `restructure/library`)

- [ ] Inventory every top-level path and every file under `docs/`: keep / move / archive-only (PLAN.md §4). Put the table in your worklog; it becomes part of the PR description.
- [ ] `archive:` commits (deletions only; `FLHUB_ALLOW_LARGE=1` allowed): `CLAUDE.md`, `.claude/`, `.githooks/`, `tools/run_agent.sh`, `docs/agents/**` except `PROTOCOL.md`, bus transcripts, `collaboration.md`, central's report sections, `REPORT.md`, `REPORT_STAGE2.md` (only after their content exists in `docs/results/` or at the tags — the tags already hold them, so deleting is safe).
- [ ] `move:` commits (`git mv` only): `docs/agents/PROTOCOL.md` → `docs/protocol.md`, `docs/DECISIONS.md` → `docs/design/decisions.md`, evidence and figures → `docs/results/...` (PLAN.md §4). Keep the framework agents' report sections (`docs/report/<fw>.md`, `docs/report/stage2/<fw>.md`) in place until the framework agents have folded them into their guides; they delete them themselves in R2.
- [ ] Docs skeleton: every page of PLAN.md §4 as a stub with its section headings, and `docs/frameworks/_template.md` with the §5.3 sections. Fix every link broken by the moves (path strings in `scripts/stage2/plots.py` etc. only where PLAN.md §3 allows; list them).
- [ ] Push, announce `pull: yes`, broadcast **S3 R1-skeleton <sha>**.

## R2 Write

- [ ] `README.md` (PLAN.md §5.1) and `docs/README.md`.
- [ ] Shared docs (PLAN.md §5.2): getting-started, architecture (with the trust and security model), protocol (links), client-contract, adding-a-framework (with implementation effort), aggregators, deployment, reproducing-results, support-matrix, limitations-and-future-work, design/decisions header, READMEs in `configs/`, `scripts/`, `deploy/`, `tools/`.
- [ ] `scripts/docs/loc.py` (non-blank, non-comment lines per component; reproducible) and `scripts/docs/check_links.py` (relative links + images in all Markdown files). Docs tooling only: reads files, writes only under `docs/`.
- [ ] **Stage-1 plots** (owner request): `scripts/docs/plots_stage1.py` from the stage-1 run directories under `~/flhub-runtime/runs/` if they still exist, otherwise from W&B project `AgnosticFL` **read-only** (`wandb.Api()`; never log, edit or delete; the runner exports `WANDB_MODE=offline`, so set `WANDB_MODE=online` for that one read-only command if the API refuses). Figures: accuracy and loss vs round for all stage-1 final runs; per-framework round/fan-out time; equivalence (max weight difference per round); federated vs centralized final metrics. Output `docs/results/figures/stage1_*.png`.
- [ ] Results (PLAN.md §5.5): `docs/results/README.md` (headline numbers + gallery), `stage1.md`, `stage2.md` (condensed, agent-free, full originals linked at the tags).
- [ ] **`docs/cost-and-efficiency.md`** (PLAN.md §5.4) — the owner's main interest. New figures at least: bytes-on-wire ratio per framework and payload, cost-model fit (`a + b·P`) per framework, lines of code per component; plus a hospital-side cost table. Ask each framework agent to send its numbers for the at-a-glance table (one `request` each) and to review its column.
- [ ] GitHub files (PLAN.md §5.6): `CHANGELOG.md`, `CITATION.cff`, `CONTRIBUTING.md`, `.github/` templates, `pyproject.toml` metadata (version 0.2.0). Optional CI only under the PLAN.md §5.6 conditions.
- [ ] Review the three framework guides (consistency with shared docs, links, no agent material) and send corrections as `request`s.
- [ ] Broadcast **S3 R2-central**.

## R3 Verify

- [ ] Run the PLAN.md §7 checklist items 1, 2, 4, 5, 6, 8 yourself; collect item 3 from flower and item 7 (`docs-ready <sha>`) from all three framework agents.
- [ ] Fix every finding; re-run. Broadcast **S3 R3-verified <sha>** with the results.

## R4 Merge and release (PLAN.md §8)

- [ ] Confirm `origin/main` is still at the end-of-stage-2 commit. If it moved, stop and send a `blocker` to human.
- [ ] PR + merge with `gh` if `gh auth status` works; otherwise the approved fast-forward push.
- [ ] Tags `v0.1.0` (stage1-final commit) and `v0.2.0` (new main head); GitHub releases if `gh` works.
- [ ] Broadcast **S3 merged <sha>**; collect `confirm main <sha>` from the three framework agents.

## R5 Close

- [ ] Export the stage-3 bus transcript (from the STAGE3-START message on) to `docs/agents/stage3/bus_transcript.md` on `archive/agentic-build` (split commits ≤ 400 lines).
- [ ] Final worklog entry; `bash deploy/central/status.sh` (read-only) shows the testbed unchanged.
- [ ] Broadcast **S3 all-done** after all three `confirm main` messages; then `touch ~/.flhub/stage3/DONE`.

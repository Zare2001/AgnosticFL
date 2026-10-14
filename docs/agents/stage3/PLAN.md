# Stage 3 — Library restructure and documentation

Stage 2 is finished (tag `stage2-final`, `REPORT_STAGE2.md`). Stage 3 does **not** run experiments and does **not** change system code. It turns `main` into a clean, documented library repository and keeps all agent material on an archive branch.

## 0. The owner's request

> Restructure the GitHub repository. `main` must hold the system — the hub and the server-side adapters, as used in stages 1 and 2 — so that people can use it as a library when they want one server that works with different FL frameworks, plus everything needed to use the framework-agnostic server. Do it the way it is done on GitHub. The documentation on `main` describes the work, but not the agent material. Do not delete the agent files and docs permanently: keep them on another branch. `main` focuses on the system and the framework, with docs that support its use and show:
> - the results, including **plots of the stage-1 runs**;
> - what it supports and what is future work;
> - **the cost of implementing it, and a good overview of the costs and efficiency of using the framework-agnostic server**;
> - how to add frameworks;
> - what the client-side code must contain to make it work, related to each framework's example code.
>
> No real code changes: this is bookkeeping and restructuring.

Owner decisions: **no license yet** (README states that no license has been chosen; do not add a LICENSE file); the **agents merge into `main` themselves** at the end (R4); all four agents take part.

## 1. Outcome

- `main` = a library repository: code unchanged, agent material removed, complete user and developer documentation, `CHANGELOG.md`, `CITATION.cff`, `CONTRIBUTING.md`, `.github/` templates, release tags.
- `archive/agentic-build` = everything as it was at the end of stage 2, plus the stage-3 bookkeeping. Nothing is lost.
- The running testbed (hub, adapters, framework servers, hospitals on all four VMs) stays exactly as it is.

## 2. Branches, checkouts and safety

| Branch | Checkout on every VM | Purpose |
|---|---|---|
| `archive/agentic-build` | `~/AgnosticFL` | Created from `main` at the end of stage 2. Holds all agent material. **Stage-3 bookkeeping** (this plan, roles, worklogs, stage-3 bus transcript) is committed **here**. The running services use this checkout; its code never changes. |
| `restructure/library` | `~/AgnosticFL-main` (git worktree) | The future `main`. **All restructuring and documentation work happens here.** |
| `main` | — | Not touched until R4. Then it is fast-forwarded or merged to `restructure/library`. |

Tags `stage1-final` and `stage2-final` never move.

Rules:

1. Never commit agent material to `restructure/library`: no `CLAUDE.md`, `.claude/`, plans, roles, worklogs, bus transcripts, or bus message ids. Agent material goes to `~/AgnosticFL` (`archive/agentic-build`).
2. Never push to `main` before R4. Never force-push. Never delete a branch or a tag. Never rewrite pushed history.
3. **The testbed is read-only.** Do not stop, start, restart, install, purge or reconfigure any service, hospital, adapter, framework server, container or systemd unit. Exception: a temporary hub core and mock adapter that you start yourself under `/tmp`, on free ports, for verification (§7), and stop again.
4. No experiment runs, no `netem`/`tc`, no fault injection, no W&B writes. **W&B is read-only** (reading the stage-1 project `AgnosticFL` for plots is allowed).
5. Git hooks: `core.hooksPath` points to `~/AgnosticFL/.githooks` (absolute), so the hooks also apply in the worktree after `.githooks/` leaves `main`.
6. Commit size: ≤ 400 changed lines per commit, ≤ 8 commits per push, as before. **Owner-approved exceptions:**
   - `FLHUB_ALLOW_LARGE=1` only for a commit that **only deletes** files or **only moves** files (`git mv`, no content change). Its message starts with `archive:` or `move:`.
   - `FLHUB_ALLOW_BIG_PUSH=1` only for the single R4 push that fast-forwards `main` (not needed if the PR is merged on GitHub).
7. Run tests against the worktree code: `cd ~/AgnosticFL-main && PYTHONPATH=$PWD:$PWD/flhub_core/src <hub-venv>/bin/python -m pytest -q`. The venvs have editable installs that point to `~/AgnosticFL`.

## 3. The no-code-change rule

Allowed on `restructure/library`:

- delete or move agent material and documentation files;
- add or edit Markdown files anywhere, and images (`*.png`, `*.svg`) under `docs/`;
- add `CHANGELOG.md`, `CITATION.cff`, `CONTRIBUTING.md`, `.github/` issue and PR templates, and an optional CI workflow (§5.6);
- `pyproject.toml`: only `[project]` metadata (`version = "0.2.0"`, `description`, `readme`, `authors`, `[project.urls]`). Never dependencies, build settings or package lists;
- new **docs tooling** under `scripts/docs/` (plot or table generators that read run data or W&B and write only under `docs/`);
- path strings in scripts that point to moved documentation or figures (each one listed in the PR description).

Forbidden: any other change to `*.py`, `*.sh`, `Dockerfile*`, configs (`*.yaml`, `*.json`, `*.env`), or anything under `hub/`, `adapters/`, `clients/`, `flhub_core/`, `tests/`, `deploy/`, `configs/`. In particular, **no `.py` file under `flhub_core/`** may change: its content hash is the hospitals' `code_version`.

## 4. Target layout of `main`

```
README.md  CHANGELOG.md  CITATION.cff  CONTRIBUTING.md  pyproject.toml  .gitignore
.github/   ISSUE_TEMPLATE/, PULL_REQUEST_TEMPLATE.md, workflows/ (optional CI)
hub/ adapters/ flhub_core/ clients/ tests/        code, unchanged (+ README.md per adapter and client folder)
configs/ scripts/ deploy/                         unchanged (+ README.md each); scripts/docs/ = new docs tooling
tools/agentbus/ tools/bus                         kept only because deploy/ and scripts/ call them (+ tools/README.md)
docs/
  README.md                    index of all docs
  getting-started.md           local quick start with the mock adapter, then a real framework
  architecture.md              components, round flow, where things run, trust and security model
  protocol.md                  flhub/0.1 + flhub/0.2 (moved from docs/agents/PROTOCOL.md)
  client-contract.md           what every hospital-side client must do (framework-independent)
  frameworks/nvflare.md        per-framework guide (§5.3)
  frameworks/vantage6.md
  frameworks/flower.md
  adding-a-framework.md        new adapter: BaseAdapter vs RelayAdapter, conformance suite, checklist, effort
  aggregators.md               supported server aggregators and how to add one
  deployment.md                deploying hub + adapters next to existing framework servers; the reference testbed
  reproducing-results.md       configs/, scripts/, deploy/ → the stage-1 and stage-2 results
  support-matrix.md            frameworks, versions, features, limits
  cost-and-efficiency.md       ★ implementation cost and runtime efficiency (§5.4)
  limitations-and-future-work.md
  design/decisions.md          the ADRs (moved from docs/DECISIONS.md)
  results/README.md            results overview and figure gallery
  results/stage1.md            condensed stage-1 results (agent-free)
  results/stage2.md            condensed stage-2 results (agent-free)
  results/figures/             stage-2 figures + NEW stage-1 figures + cost figures
  results/evidence/            stage-1 and stage-2 evidence files, runs.md, tables, ledger
```

| Path today | On `main` |
|---|---|
| `CLAUDE.md`, `.claude/`, `.githooks/`, `tools/run_agent.sh` | removed (archive only) |
| `docs/agents/**` | removed, except `PROTOCOL.md` → `docs/protocol.md` |
| `docs/DECISIONS.md` | → `docs/design/decisions.md` (add a header: bus references like `#123` point to the archive branch) |
| `REPORT.md`, `REPORT_STAGE2.md` | removed; condensed into `docs/results/stage1.md` / `stage2.md`; the full originals are linked at the tags (`.../blob/stage1-final/REPORT.md`, `.../blob/stage2-final/REPORT_STAGE2.md`) |
| `docs/report/**` per-agent sections (`<fw>.md`, `stage2/<fw>.md`) | folded into `docs/frameworks/<fw>.md` by the framework agents |
| `docs/report/**` bus transcripts, `collaboration.md`, `central.md`, `stage2/central.md` | removed (archive only) |
| `docs/report/evidence/**`, `docs/report/stage2/{evidence,figures,runs.md,tables.md,cost_model.md,e4_bytes.md,e6.md,tuning.md,ledger.md}` | → `docs/results/{evidence,figures,...}`; `ledger.md` → `docs/results/evidence/hospital-ledger.md` |

When in doubt, keep a file on `main` if code on `main` needs it at runtime; otherwise archive it.

## 5. Content specification

**Audience:** engineers and researchers who want one server across NVFlare, vantage6 and Flower. Plain, precise English. Every number comes from an evidence file, `runs.md` or the stage reports; cite the file next to each table. Units are explicit (GiB vs GB, s per round). Real VM IP addresses appear only in `deploy/` (reference testbed); docs use placeholders such as `<central-host>`. No agent roles, bus message ids or agent workflow in any document on `main`. One neutral line in the README points to the archive branch and tags for the full development history.

### 5.1 `README.md` (central)

1. One-paragraph pitch: what it is, for whom, what problem it solves.
2. Key results in 4–6 bullets with numbers, e.g.: bit-identical to a framework-free simulation in all 69 real runs; hospital-side code changes 0 lines; overhead against native FedAvg; payload sizes tested; recovery without manual action.
3. Architecture diagram (keep the ASCII one or a Mermaid diagram) and a short round description.
4. Support matrix summary (frameworks + tested versions, aggregators, key features) → `docs/support-matrix.md`.
5. Quick start (mock adapter, one machine) → `docs/getting-started.md`.
6. Cost and efficiency at a glance (a small table and one figure) → `docs/cost-and-efficiency.md`.
7. Documentation index; repository layout table.
8. Project status (research prototype, tested versions), Citation (`CITATION.cff`), **License: "No license has been chosen yet. Until a LICENSE file is added, all rights are reserved by the author."**, Acknowledgements (SURF), history line (archive branch + tags).

### 5.2 Shared docs (central)

- `getting-started.md`: install, run hub core + mock adapter + orchestrator + sim + equivalence check + tests; then pointers to the framework guides. Every command must work verbatim (§7).
- `architecture.md`: components and responsibilities; one round step by step; determinism and `flhub_core`; partial participation, deadlines, resume; **trust and security model** (the hub sees every model update; transport security per framework as deployed in the testbed; no secure aggregation or DP; what a production deployment must add).
- `protocol.md`: moved file; fix links; keep content.
- `client-contract.md`: the framework-independent contract: what the client receives (weights, `train_config`, optional `data_variant`), what it must run (`flhub_core.train`), what it must return (weights, `num_examples`, metrics incl. `data_fp`, `code_version`), determinism requirements, data layout and variants, how new library versions reach hospitals. Link each item to the per-framework example code.
- `adding-a-framework.md`: when to use `BaseAdapter` vs `RelayAdapter`; the methods to implement; flhub/0.2 features (`report_site`, per-site cancel, partial collect); the conformance suite v2 as the admission test; deploy scripts; a checklist; **implementation effort** measured on the three existing adapters (lines of code, files, framework concepts touched).
- `aggregators.md`: the six aggregators, their parameters, tuned values from stage 2, the divergence findings (FedAvgM step size), how to add one.
- `deployment.md`: deploying the hub and an adapter next to an **existing** framework server; ports, credentials, health checks; then the reference 4-VM testbed (`deploy/`) and what each script does.
- `reproducing-results.md`: how `configs/`, `scripts/` and `deploy/` reproduce stage 1 and stage 2; the equivalence check (INV-1).
- `support-matrix.md`: frameworks and tested versions; Python/torch versions; protocol versions; features (FedAvg + 5 aggregators, partial participation, `min_sites`/deadline, per-site cancel, resume, data variants, sites tested up to 12, mixed federations); measured limits per framework; explicitly **not supported** (secure aggregation, DP, GPUs, robust aggregators, SCAFFOLD, federated evaluation at sites, …).
- `limitations-and-future-work.md`: testbed limits (CPU-only VMs, one data centre, 18 GB disk), method limits (one seed for real runs, ResNet-18 only 20 rounds), framework findings that need upstream work, and a prioritised future-work list.
- `design/decisions.md`: moved; header note.
- `docs/README.md`: index with one line per page.
- `README.md` files in `configs/`, `scripts/`, `deploy/`, `tools/` (what is there, who needs it). `tools/README.md` describes the bus as the reference testbed's small authenticated message and file bus used by `deploy/` and `scripts/`.

### 5.3 Framework guides `docs/frameworks/<fw>.md` (one per framework agent)

Use these sections, in this order:

1. **Summary**: tested version, status, what the adapter does in two sentences, rollout channel.
2. **Server side — how the adapter maps flhub onto <framework>**: components (adapter service, relay job/run or tasks), how a round is executed, ports, configuration, start/stop/health, how sites are discovered and identified.
3. **Client side — what the hospital code must contain**: walk through the example in `clients/<fw>/` with short quoted snippets (the essential lines only); mark what is the framework's standard pattern and what is the flhub-specific part; how the client gets data and data variants; packaging and **rollout** of new library versions; the hospital-side change ledger (code lines, config, ops steps).
4. **Deploying next to an existing <framework> federation**: steps and credentials.
5. **Cost and efficiency for <framework>**: adapter and client lines of code; rollout steps, time and downtime; overhead against native FedAvg (E7); fixed cost per round and cost per MB (cost model); bytes on the wire ÷ payload; memory per component; bandwidth efficiency (E4b); scaling (E5); detection and recovery times (E6). Link the figures in `docs/results/figures/`.
6. **Limits, known issues and knobs**: measured limits, failure modes found, server-side vs hospital-side knobs.
7. **Troubleshooting**: the problems met in stages 1–2 and their fixes, as short symptom → cause → fix entries.
8. **Future work and upstream reports** for this framework: what would lift the limits; draft texts for upstream issues where a framework bug was found (not filed).

Also add `adapters/<fw>/README.md` and `clients/<fw>/README.md` (5–15 lines each, pointing to the guide). Sources: your stage-1 and stage-2 report sections (`docs/report/<fw>.md`, `docs/report/stage2/<fw>.md`), the ledger, `docs/report/stage2/evidence/<fw>/`, the ADRs, your worklogs on the archive branch.

### 5.4 `docs/cost-and-efficiency.md` (central; framework agents supply and check their numbers)

The owner wants **a good overview of the costs and efficiency of using the framework-agnostic server**. Structure:

1. **At a glance**: one table, one row per cost dimension, one column per framework (+ hub-wide), each cell a measured number with its source.
2. **Implementation cost**: lines of code per component (hub core, orchestrator, aggregators, sim, `adapters/common`, each adapter, `flhub_core`, each client), counted reproducibly (`scripts/docs/loc.py`, non-blank non-comment lines); tests; effort to add a framework (link `adding-a-framework.md`).
3. **Integration cost at the hospitals**: code lines changed (0), configuration and ops steps, rollout time and downtime per framework.
4. **Runtime overhead**: hub against native FedAvg (E7: seconds and %); round-time breakdown (train / transport+framework / hub); cost model `a + b·P` per framework with the fit quality; fixed per-round floors and their causes.
5. **Network efficiency**: bytes on the wire ÷ payload (E4a); bandwidth efficiency against the lower bound (E4b), including vantage6's protocol-adjusted bound.
6. **Memory and disk**: peak RSS per component and VM against payload (E4a); disk growth of framework stores and the purge steps.
7. **Scaling**: 6 → 12 sites (E5); mixed federations are paced by the slowest framework.
8. **Robustness cost**: detection and recovery times, rounds retried (E6).
9. **Learning efficiency**: rounds-to-target and accuracy against centralized training (E1/E2), and stage 1 vs centralized.
10. **When is it worth it**: interpretation and recommendations per framework.
11. **Method and caveats**: hardware, one data centre, testbed disk limits, seeds.

Figures: reuse the stage-2 figures (`e7_native_vs_hub`, `e4a_round_time_vs_payload`, `e4a_peak_rss_vs_payload`, `e4b_measured_vs_bound`, `e3_round_time_breakdown`, `e5_scaling`) and add at least: a **bytes-on-wire ratio** chart, a **cost-model fit** chart, a **lines-of-code per component** chart, and a **hospital-side cost per framework** table.

### 5.5 Results (central)

- `results/stage1.md` and `results/stage2.md`: condensed (≈150–300 lines each), agent-free: setup, research questions, results with tables and figures, threats to validity. Link the full originals at the tags.
- **New stage-1 figures** (owner request), made by `scripts/docs/plots_stage1.py` from the stage-1 run data (local run directories if they still exist, otherwise the W&B project `AgnosticFL`, **read-only**): test accuracy and loss vs round for every stage-1 final run in one chart; per-framework round-time / fan-out time; the equivalence result (max weight difference per round, a table or chart); centralized vs federated final metrics. Write them to `docs/results/figures/stage1_*.png` and use them in `stage1.md`, `results/README.md` and `cost-and-efficiency.md`.
- `results/README.md`: headline numbers of both stages and a figure gallery with one-line captions.
- Move evidence and figures (§4) and fix every link.

### 5.6 GitHub files (central)

`CHANGELOG.md` (Keep a Changelog style: `0.1.0` = stage 1, flhub/0.1; `0.2.0` = stage 2, flhub/0.2, aggregators, participation, resume; `Unreleased`/`0.2.0` note on the restructure), `CITATION.cff` (author Zare Palanciyan, SURF; title; version 0.2.0; repository URL; no license field), `CONTRIBUTING.md` (dev setup, tests, conformance suite, adding an adapter or aggregator, docs style, commit style), `.github/ISSUE_TEMPLATE/` (bug report, framework request), `.github/PULL_REQUEST_TEMPLATE.md`. Optional `.github/workflows/ci.yml` (unit tests + mock conformance on Python 3.11, CPU torch): add it only if the exact steps pass in a fresh venv on a VM; after the merge, check the first run (`gh run list` if `gh` works) and remove the workflow if it fails for reasons outside the workflow file.

### 5.7 Ownership of files

Central owns every file except `docs/frameworks/<fw>.md`, `adapters/<fw>/README.md` and `clients/<fw>/README.md`, which the framework agent `<fw>` owns. To change a file you do not own, send a `request` with the exact text. Pull before every push (`git -C ~/AgnosticFL-main pull --rebase`).

## 6. Phases and milestones

| Phase | Who | Content | Milestone |
|---|---|---|---|
| **R0 Kickoff** | all | Read this plan and your role file. Check: `~/AgnosticFL` on `archive/agentic-build`, `~/AgnosticFL-main` on `restructure/library` and clean, hooks path, ≥ 3 GB free, your testbed status (read-only). Post a health line. | `S3 R0-ready` (central, after three health posts) |
| **R1 Skeleton** | central | Archive deletions, moves (§4), `docs/` skeleton with stub pages and the framework-guide template (§5.3), link fixes. Framework agents meanwhile collect their facts and numbers (no shared-file edits). | `S3 R1-skeleton <sha>` |
| **R2 Write** | all | Central: README, shared docs, results, stage-1 plots, cost-and-efficiency, GitHub files. Framework agents: their guide + READMEs; flower also verifies the quick start (§7). Cross-review (§7). | `S3 R2-<role>` per agent |
| **R3 Verify** | central; framework agents confirm | Checklist §7. Framework agents send `docs-ready <sha>` after checking every statement about their framework. | `S3 R3-verified <sha>` |
| **R4 Merge and release** | central | §8. Framework agents verify `main` from a fresh clone and send `confirm main <sha>`. | `S3 merged <sha>` |
| **R5 Close** | all | Stage-3 bus transcript and final worklogs on `archive/agentic-build`; status check of your testbed (unchanged). | `S3 all-done` (central), then each agent's DONE |

## 7. Verification checklist (R3)

1. Tests from the worktree: unit tests and the mock conformance suite pass (§2 rule 7; temporary services under `/tmp` on free ports).
2. `python -m flhub_core.selfcheck` from the worktree prints the stage-2 digest `sha256:ac074a0b…`.
3. **Quick start verbatim** (flower agent): a fresh venv and a fresh clone of `restructure/library` under `/tmp` on the flower VM; every command in `docs/getting-started.md` exactly as written. Report the output; fix the doc, not the code.
4. Links: every relative link and image in every `*.md` on the branch resolves (`scripts/docs/check_links.py`); every figure in `docs/results/figures/` is referenced at least once.
5. Greps on the branch tree (outside `deploy/`, `scripts/`, `tools/`, `configs/`): no `CLAUDE`, `.claude`, `worklog`, `bus_transcript`, `docs/agents`, agent-role wording, bus message ids (`#[0-9]{2,4}` used as a reference), real IP addresses (`145.38.`), or secret patterns.
6. No-code-change check (§3): `git diff --stat origin/main...origin/restructure/library -- . ':!docs' ':!*.md' ':!.github' ':!CITATION.cff' ':!scripts/docs'` lists only archived deletions, `pyproject.toml` metadata and the listed path strings. Paste it into the PR description.
7. Numbers: every framework agent checks every statement about its framework (README, support matrix, cost doc, results) and its own guide against the evidence.
8. All commits on the branch are authored and committed by Zare2001, with no attribution lines.

## 8. Merge and release (R4, central)

1. If `gh auth status` works: open a PR `restructure/library` → `main` with a full description (what moved where, what was archived and where to find it, the §7 results, the no-code-change diff stat), then merge it with a merge commit (`gh pr merge --merge`). Otherwise fast-forward: `git -C ~/AgnosticFL-main push origin restructure/library:main` with `FLHUB_ALLOW_BIG_PUSH=1` (owner-approved for this push only). `main` must not have moved since stage 2; if it did, stop and ask the owner.
2. Tags (annotated, never moved later): `v0.1.0` on the commit of `stage1-final`, `v0.2.0` on the new `main` head. If `gh` works, create GitHub releases for both with notes from `CHANGELOG.md`.
3. Announce `S3 merged <sha>`. Framework agents clone `main` fresh under `/tmp`, run the link check and the unit tests, and reply `confirm main <sha>`.

## 9. Definition of Done

1. `archive/agentic-build` exists on GitHub and contains the full end-of-stage-2 tree plus the stage-3 bookkeeping; tags `stage1-final` and `stage2-final` are unchanged.
2. `main` has the layout of §4, no agent material, and all documents of §5, including `docs/cost-and-efficiency.md` with its figures, the **stage-1 plots**, the three framework guides with client requirements and example code, `adding-a-framework.md`, `support-matrix.md` and `limitations-and-future-work.md`.
3. The §7 checklist passes on `main` (fresh clone): tests, selfcheck digest, quick start, links, greps, no-code-change diff.
4. Tags `v0.1.0` and `v0.2.0` are pushed (releases if `gh` works); `CHANGELOG.md`, `CITATION.cff`, `CONTRIBUTING.md` exist; the README states that no license has been chosen yet.
5. All commits are by Zare2001 without attribution; the testbed on every VM is as it was at the start of stage 3 (your status script, read-only).
6. Stage-3 bus transcript and final worklogs are on `archive/agentic-build`; central broadcast `S3 all-done`; every agent created `~/.flhub/stage3/DONE`.

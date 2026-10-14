# Stage 2 report template

Central compiles `REPORT_STAGE2.md` (repo root) with the numbered sections below. Each framework agent writes `docs/report/stage2/<role>.md` in the per-agent format, and central includes those sections in §10. Every number in the report comes from a final run listed in `docs/report/stage2/runs.md` (with its W&B URL) or from a measured log, and the source is cited.

---

## REPORT_STAGE2.md

### 1. Executive summary (≤ 1 page)
- Answer RQ1–RQ5 in one or two sentences each, with the headline numbers.
- State whether the headline claim (PLAN.md §1) held.
- Give the total campaign time and the number of final runs.
- List the three most important limits found.

### 2. Setup
- What was reused from stage 1 and what is new (link REPORT.md).
- Versions (`deploy/versions.env`, `flhub_core` 0.2 code_version) and VM specs.
- Dataset ADR: source, license, checksum, class count and split sizes.
- Partition definitions, with the site × class heat map per pid.
- Model zoo table: parameters, payload MB, selfcheck v2 hash.
- Aggregator formulas (PLAN.md §4.1) and the tuned hyperparameters (link `tuning.md`).

### 3. RQ1 — Correctness (INV-1)
Table of every final real run with:
- INV-1 method (full sim / replay / participants replay);
- max abs weight difference;
- final accuracy difference.

List and explain every exception. Show that E6d's resume equals the uninterrupted run bit-exactly. Report the INV-4 re-run check.

### 4. RQ2 — Hospital-side cost
- The ledger (embed or link `ledger.md`).
- `git diff --stat stage1-final..HEAD -- clients/` and `-- adapters/`.
- Rollout comparison per framework: channel (job / image / FAB), steps, wall time, restarts, downtime, manual hospital actions.
- Which changes were hub-only (target: aggregators, participation, resume, models).

### 5. RQ3 — Learning under heterogeneity (E1, E2, E3 accuracy)
For each experiment:
- table: final test accuracy, balanced accuracy, macro AUC, worst-site accuracy and rounds-to-target, with mean ± std over 3 seeds where available, marked "real" or "sim";
- curves;
- interpretation.

Compare with centralized and local-only training. Discuss framework-correlated skew (`fwcorr`) against site-level skew. State clearly that seeds 2027/2028 come from `hub.sim`, justified by INV-1.

### 6. RQ4 — Performance (E3 timing, E4, E5, E7)
- Round-time breakdown per framework and model: train, overhead, aggregation, eval.
- Payload sweep:
  - time vs size (log–log);
  - bytes overhead ratio;
  - peak RSS;
  - per framework, the max size carried, the failure mode, and the knob with its side.
- Bandwidth: measured vs lower bound, and efficiency per framework.
- Scaling 6 → 12 sites: per-framework fan-out and central resources.
- Native vs hub (E7): weight difference and overhead in seconds and %, per framework.
- A simple cost model fitted to the data, `round_time ≈ a + b·payload/bandwidth + max_k train_k`, with its fit quality.

### 7. RQ5 — Robustness (E6)
Scenario × framework table:
- outcome;
- detection time;
- recovery time;
- rounds lost or retried;
- manual action;
- INV-1.

Include a timeline figure of one chaos run. Note which behaviours come from the hub and which from each framework.

### 8. Threats to validity
- IID-like hardware (same CPU type everywhere).
- Shared datacenter network (no real WAN except netem).
- Simulated hospitals on 3 VMs (co-located sites share CPU).
- One dataset; tuning on one pid.
- Sim-based seed statistics.
- netem emulation fidelity.
- Anything else found.

### 9. Limitations, security notes, future work
- Out-of-scope items (PLAN.md §3): SCAFFOLD, robust aggregators, federated evaluation, secure aggregation, DP.
- Plaintext updates at the hub.
- TLS gaps (vantage6 and Flower without TLS).
- Recommended next steps.

### 10. How the agents ran the campaign
- Timeline of the S-milestones (UTC).
- RUNLOCK usage and problems.
- Per-agent sections (`docs/report/stage2/*.md`).
- Collaboration statistics: commits per scope, bus messages by type.
- Subagent usage.

### 11. Reproduce
Exact commands to reproduce one E1 run, one E4a run and one E6d run from the frozen SHA on the deployed system, and from fresh VMs (reference REPORT.md §11 for the base install).

### 12. Definition of Done — evidence
Each item of PLAN.md §9, with the command run and its trimmed output.

### Appendix
- Full `runs.md` table with W&B URLs.
- Stage-2 ADR list.
- Bus transcript link.
- Figure index.

---

## Required figures (`docs/report/stage2/figures/`, PNG ≤ 300 KB)

1. Partition heat maps (one panel per pid).
2. E1: test accuracy vs round per pid.
3. E1/E2: worst-site (site-shaped) accuracy.
4. E2: final accuracy, aggregator × pid with seed error bars.
5. E2: accuracy vs round for `dir0.1`, per aggregator.
6. E3: round-time breakdown (stacked bars) per model and framework.
7. E4a: round time vs payload size (log–log) per framework, with failure markers.
8. E4a: peak RSS vs payload size.
9. E4b: measured vs lower-bound round time per profile.
10. E5: round time and fan-out vs number of sites.
11. E6: timeline of injections and recovery.
12. E7: hub vs native round time per framework.

---

## Per-agent section (`docs/report/stage2/<role>.md`)

1. **Scope** — what you owned in stage 2.
2. **What changed** — files, ADRs, ledger rows (framework agents), and `git diff --stat stage1-final..HEAD` for your directories.
3. **Rollout** (framework agents) — the exact procedure, cost measurements, and what a real hospital would have to do.
4. **Limits and knobs** — payload findings with evidence.
5. **Experiments you ran or supported** — E4/E6/E7 results from your side, with log excerpts.
6. **Problems and fixes** — table: problem, symptom, root cause, fix, SHA, time lost.
7. **Recommendations**.

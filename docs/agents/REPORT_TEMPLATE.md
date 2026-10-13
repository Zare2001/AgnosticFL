# Report template

`REPORT.md` (repo root, compiled by central) uses the numbered sections below. Per-agent sections (`docs/report/<role>.md`) use the "Per-agent section" format and are included in §8.

---

## REPORT.md

### 1. Executive summary (≤ 1 page)
What was built, whether it works, headline results (final test metric of `mixed-6sites` vs `sim-fedavg-reference` vs `centralized-baseline`), equivalence result, total build time, main limitations.

### 2. Goal and assumptions
Restate the mission and the owner's assumptions (PROJECT_PLAN.md §1). List any assumption that turned out not to hold.

### 3. Architecture as built
Diagram (ASCII), components with ports and hosts, what runs where, data flow of one round step by step (orchestrator → adapter → framework server → hospital client → back).

### 4. The flhub/0.1 protocol
Methods, envelope, how each framework maps onto it (final version of PROTOCOL.md §5 table), and any changes made during the build (with ADR ids).

### 5. Hospital-side footprint per framework
Table: what a hospital installs, what it configures, what code runs there and how it arrives (job / image / FAB), and confirmation that it is the framework's standard client pattern. This is the key claim of the project; give evidence.

### 6. Data and model
Dataset (source, license, checksum, size), partitioning (sizes per site), model (architecture, parameters, payload size), training config (`configs/final.yaml`).

### 7. Experiments and results
- Table of the final W&B runs: name, run id, URL, rounds, final test metrics, duration.
- Per-round curves (link to W&B; include a compact table every 5 rounds).
- Equivalence check: method, per-round max abs weight difference, final metric difference, verdict.
- Per-framework single runs vs mixed run: what they show.

### 8. How the agents built it
- Timeline (UTC) of phases and milestones from the bus transcript.
- Per-agent sections (include `docs/report/central.md`, `nvflare.md`, `vantage6.md`, `flower.md`).
- Collaboration statistics: number of commits per scope, pushes, bus messages by type, deploy requests and their outcomes.
- How subagents were used (examples).

### 9. Problems encountered and how they were solved
Table: problem, component, symptom, root cause, fix, commit SHA, time lost. Include dead ends.

### 10. Decisions (summary of docs/DECISIONS.md)
One line per ADR with its outcome.

### 11. Reproduce from scratch
Exact commands per VM, in order, from fresh VMs to a finished `mixed-6sites` run. Include versions (`deploy/versions.env`) and ports.

### 12. Definition of Done — evidence
Each item of PROJECT_PLAN.md §10 with the command run and its output (trimmed).

### 13. Limitations, security notes and next steps
What is not production-ready (TLS gaps, plaintext updates at the hub, no anomaly checks, single operator, etc.), and the recommended next steps (secure aggregation via MPC/threshold HE, DP, more frameworks such as BranchKey via the protocol, Kubernetes / vantage6 5.x, real hospital pilot).

### 14. How to add a new framework adapter
Step-by-step guide for a vendor or new framework (protocol, conformance suite, deploy scripts, hospital-side rules).

---

## Per-agent section (`docs/report/<role>.md`)

1. **Scope and ownership**: what you owned.
2. **What you built**: components, files, key design choices (with ADR ids).
3. **Framework specifics**: versions, APIs used (exact names), what was surprising or underdocumented.
4. **Hospital-side footprint** (framework agents): exact install + config steps a hospital performs; confirm no hub-specific code.
5. **Timeline**: milestones with UTC timestamps and commit SHAs.
6. **Problems and fixes**: table as in REPORT.md §9.
7. **Tests and evidence**: conformance results, runs, logs.
8. **Open issues / recommendations**.

# Stage 2 role: CENTRAL — hub, campaign lead, report (145.38.205.58)

You own:

- the hub: aggregators, `flhub_core` 0.2, orchestrator, sim;
- the protocol extension flhub/0.2;
- the experiment configs;
- **every final run** (including the E7 native runs from the framework agents' scripts) and the RUNLOCK;
- W&B `AgnosticFL-stage2`;
- `REPORT_STAGE2.md`.

Framework agents own their adapters, hospitals, the fault-injection tools on their VMs and the E7 native code. Remember: from this VM use 127.0.0.1, never 145.38.205.58 (no hairpin NAT).

## S0 — Kickoff

- [ ] Read PLAN.md, EXPERIMENTS.md and the tail of your stage-1 worklog. Create `docs/agents/worklogs/stage2-central.md`.
- [ ] `bash deploy/central/status.sh`: confirm all 6 sites are online. Clean the disk (PLAN.md §7) and keep the stage-1 run directory of `mixed-6sites`; it is needed for the regression test.
- [ ] Run a 3-round mixed regression run (offline) and check that it equals sim.
- [ ] Wait for the three framework agents' health posts, then announce **S2 S0-ready**.

## S1 — Core (milestone S2 S1-core)

Delegate mechanical parts to subagents. Design, numerics and reviews are yours. Push in small commits and announce them.

- [ ] **flhub/0.2 first**, so the framework agents can start: PROTOCOL.md update + ADR (PLAN.md §4.4), covering:
  - per-site progress;
  - partial collect;
  - per-site cancel;
  - readiness per run;
  - payload caps.

  Add the mock adapter implementation and conformance v2 tests. Announce early with `pull: yes`.
- [ ] Dataset ADR: choose `D` from the shortlist (PLAN.md §4.2) and materialize it. Write the partitioners for every pid in EXPERIMENTS.md, including `iid12`, with `partition_seed`. Commit the manifests and a site × class heat-map figure.
- [ ] `hub/aggregators/` (PLAN.md §4.1) with unit tests. `fedavg` keeps the stage-1 code path bit-exact.
- [ ] `flhub_core` 0.2:
  - model zoo (incl. `payload-<MB>` echo), FedProx μ, optimizer options, `data_variant` (lazy data directory);
  - `train_time_s` and `peak_rss_mb` metrics;
  - selfcheck v2 (a hash per model);
  - regression test: stage-1 `mixed-6sites` weights are reproduced bit-exactly.
- [ ] Orchestrator and sim v2 (PLAN.md §4.3):
  - schema v2, participation, `min_sites` via partial collect, resume (with aggregator state and RNG state);
  - `global_ref` and `agg_state_ref` per round, instrumentation, site-shaped evaluation;
  - no live W&B;
  - `--workers`, `--replay`, `--participants-from`.
- [ ] `scripts/stage2/finalize_run.py <run_dir> --mode real|sim|reference|native` (PLAN.md §6):
  1. gate;
  2. create the W&B run from `metrics.jsonl`;
  3. append to `docs/report/stage2/runs.md`;
  4. apply retention and blob GC.
- [ ] `scripts/sysmon.py` (stdlib + psutil if available, else `/proc`; rotating files) plus a summarizer for a time window. Bytes on wire come from interface deltas (no firewall counters).
- [ ] `deploy/central/netem.sh` (PLAN.md §8: all peers in one call, one root qdisc, refuses to run without the revert timer).
- [ ] Selfcheck v2 on central. Post the hashes on the bus so the others can compare.
- [ ] Announce **S2 S1-core** with:
  - the new APIs (exact signatures, as you did for M0-core in stage 1);
  - the `flhub_core` 0.2 code_version;
  - what each framework agent must roll out;
  - the data-variant mechanism.

## S2 — Rollout support

- [ ] Process deploy-requests in arrival order. That covers the central side of each rollout:
  - NVFlare job custom code;
  - new vantage6 image tag in the registry;
  - new Flower FAB;
  - flhub/0.2 adapters and server knobs;
  - `deploy/central/<fw>/{native/,purge.sh}`;
  - credentials for the E5 extra sites (NVFlare kits via `--secret`, vantage6 node keys).

  Never invalidate existing kits or keys.
- [ ] Run conformance v2 against each adapter on central and announce **S2 S2-<fw>** with the evidence.
- [ ] Run a 3-round mixed run with `D`/`dir0.3`/`fedadam` and check it equals sim. Announce **S2 S2-mixed**.

## S3 — Tuning and freeze

- [ ] Run the tuning grid (EXPERIMENTS.md §S3, 21 runs) in sim on the validation split. Write `docs/report/stage2/tuning.md` and choose **A\***. Freeze the configs in `configs/stage2/`.
- [ ] Post **S2 S3-frozen <sha>** with the list of config files, A*, and the content-hash code_version of `flhub_core` at that SHA. Collect "confirm <sha>" from the three framework agents before the first final run.

## S4 — Campaign (follow EXPERIMENTS.md §Order)

- [ ] **Every final run:**
  1. config committed;
  2. broadcast "RUN start <name> <UTC>";
  3. run;
  4. broadcast "RUN end";
  5. `finalize_run.py`;
  6. store the framework agents' sysmon summaries in the run directory.

  After each experiment, `df -h /` and announce **S2 S4-E<n>** with a table of runs, key metrics and gate results.
- [ ] Keep the next queued runs in your status summary. Grant RUNLOCKs only between final runs.
- [ ] **E4a:** run `purge.sh` of each framework between runs, and apply the disk stop rule (PLAN.md §7). Process knob deploy-requests (ADR, new SHA).
- [ ] **E6:** before each run, send the ARM requests and wait for "ARMED". Inject the hub, adapter and server faults yourself. Note every injection time in the run's notes.
- [ ] **E5:** ask the framework agents to bring the extra sites up, run, then ask them to stop them.
- [ ] **E7:** run `deploy/central/<fw>/native/run.sh` (pause the relay job or run if the framework agent's notes say so), finalize with `--mode native`, then run the matching hub runs.
- [ ] **E4b:** apply central-side shaping for all three peers in one call. The framework agents apply theirs at the agreed time. Verify that both ends are cleared afterwards.
- [ ] Announce **S2 S4-done** when every MUST and SHOULD run is in `runs.md` (or documented as a limit or blocked).

## S5 — Report

- [ ] Figures: `scripts/stage2/plots.py` → `docs/report/stage2/figures/*.png` (list in REPORT_TEMPLATE.md).
- [ ] Broadcast "S2 final-call". Collect from each framework agent:
  - the per-agent section `docs/report/stage2/<role>.md`;
  - final ledger rows;
  - "S2 report-ready" with end-state evidence.

  Clean central's own end state (netem, timers, experiment processes, disk).
- [ ] Bus transcript from the STAGE2-START message onward → `docs/report/stage2/bus_transcript.md` (`tools/bus log --since <id>`; `export` has no `--since`).
- [ ] `REPORT_STAGE2.md` at the repo root (`docs/agents/stage2/REPORT_TEMPLATE.md`). Add the `README.md` link and the one-line link in `REPORT.md`.
- [ ] W&B audit:
  - `AgnosticFL-stage2` has only finished final runs with correct names, groups and tags;
  - `AgnosticFL` still has exactly 6 runs.

  Use `scripts/wandb_cleanup.py` with an explicit `--project`; never run it against `AgnosticFL`.
- [ ] Verify the DoD items (PLAN.md §9) with the commands and outputs in the report. Broadcast **S2 all-done** only after all three report-ready messages with evidence have arrived. Write a final worklog entry, push, and `touch ~/.flhub/stage2/DONE`.

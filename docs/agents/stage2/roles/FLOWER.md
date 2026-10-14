# Stage 2 role: FLOWER — Flower adapter + hospitals (145.38.187.20)

Through all of stage 2 you:

- keep `flw-h5` and `flw-h6` running standard Flower SuperNodes;
- extend the Flower adapter to flhub/0.2;
- roll out `flhub_core` 0.2 the Flower way (the FAB);
- find Flower's payload limits;
- provide fault-injection tools on your VM;
- deliver the Flower native FedAvg baseline (E7).

Central runs every final run; you have no shell on central, so anything that must run there goes through a committed script plus a deploy-request.

## S0 — Kickoff

- [ ] Read PLAN.md, EXPERIMENTS.md and your stage-1 worklog tail. Create `docs/agents/worklogs/stage2-flower.md`.
- [ ] `deploy/flower-vm/status.sh`: both SuperNodes are connected to central and the supervisor loop and watchdog are active. Clean the disk (PLAN.md §7). Post an `info` with the health result and free disk.

## S1 — Preparation (in parallel with central; do not touch the central deployment)

- [ ] **Payload research** (scout, against the installed Flower 1.39.0 source):
  - gRPC max message size on the Fleet API (SuperLink ↔ SuperNode), and whether `ArrayRecord` or messages are chunked or streamed;
  - SuperLink database growth with large messages (`--database` file);
  - limits on the SuperLink API that the relay ServerApp and `flwr run` use (127.0.0.1:9093, per ADR-007).

  Write it in your worklog as "knob, file, side (server/hospital), default". Prepare the server-side knobs for S2.
- [ ] **Adapter: flhub/0.2** (PLAN.md §4.4), once central has specified it. Test it on an island:
  - per-site progress, partial collect, per-site cancel, then an immediate next dispatch;
  - readiness per run (registered but offline `flw-h11`/`flw-h12` do not block);
  - payload cap ≥ 1024 MB;
  - relay run relaunched automatically after a SuperLink restart.
- [ ] **Rollout plan** for `flhub_core` 0.2. Preferred: the FAB carries it, so hospitals need no manual step. Check what a FAB may contain, how SuperNodes run with `--disable-runtime-dependency-installation`, and import precedence versus the hospital venv's `flhub_core` 0.1. ADR.
- [ ] **E6 tools on your VM** (EXPERIMENTS.md E6): `deploy/flower-vm/inject.sh` (arm/disarm; kill a SuperNode or its ClientApp process at round N + S seconds, triggered by the local log) and `deploy/flower-vm/straggler.sh` (CPU-throttle one SuperNode, for example restarted inside a CPUQuota scope). Test both on your island.
- [ ] **E7 native baseline:**
  - a ServerApp with the `FedAvg` strategy (weighted by `num-examples`) and the same ClientApp `@app.train`;
  - packaged as `deploy/central/flower/native/run.sh <config> <out_dir>`, which starts it as a separate run on the central SuperLink and writes `metrics.jsonl` in the hub's format;
  - document whether the hub relay run must be paused;
  - dry-run on your island.
- [ ] **E5 scripts:** SuperNodes `flw-h11`/`flw-h12` (same start, stop and supervisor pattern; data path and site id via node config).
- [ ] `deploy/central/flower/purge.sh` compacts or cleans the SuperLink DB and old run data on central (for E4a).
- [ ] `deploy/flower-vm/netem.sh` (PLAN.md §8): egress toward 145.38.205.58:9092 only; revert timer mandatory. Test it with a 1-minute profile under a RUNLOCK, then clear it.
- [ ] Start `scripts/sysmon.py` as soon as central pushes it; keep it running through S4.

## S2 — Rollout (milestone S2 S2-flower, announced by central)

- [ ] Provision all data variants for `flw-h5`/`flw-h6` (later h11/h12), sha256-verified. Point `data-path` at the directory if the data-variant design needs it (node config only; ledger row).
- [ ] Send a deploy-request for:
  - the new FAB;
  - adapter flhub/0.2 and relay;
  - server knobs;
  - `native/` and `purge.sh`.

  After deployment:
  - selfcheck v2 in the SuperNode runtime;
  - conformance v2 passes against central (central runs it).
- [ ] Measure rollout time, steps, SuperNode restarts and downtime for the RQ2 comparison. Add the ledger rows.
- [ ] `git diff --stat stage1-final..HEAD -- clients/flower` goes in your worklog; explain any non-zero line.

## S3 / S4

- [ ] Confirm the S3-frozen SHA (PLAN.md §5): the FAB in use contains the frozen content-hash code_version, the diff is empty, and `status.sh` is HEALTHY.
- [ ] Watch the SuperNode logs during every run. Report anomalies immediately.
- [ ] For every "RUN end" from central, send the sysmon JSON summary of that window (`tools/bus put <file> --to central`).
- [ ] **E4a:** on failure, propose a server-side knob (deploy-request, ADR) for one retry, then record the limit with the evidence.
- [ ] **E4b:** apply and clear your netem profile at the times central requests. Confirm both on the bus.
- [ ] **E6:** answer every ARM request: arm, reply "ARMED", confirm the outcome and the recovery after the run, then disarm. In E6e, check that the SuperNodes reconnect after the SuperLink restart without manual action.
- [ ] **E5:** start `flw-h11`/`flw-h12` when central asks; stop them afterwards.
- [ ] **E7:** help central run `native/run.sh`; analyse native vs hub for your report section.

## S5

- [ ] On "S2 final-call", first the end state on your VM:
  - netem cleared;
  - no `flhub-netem*` timers;
  - injection and straggler tools off;
  - extra SuperNodes stopped;
  - base SuperNodes HEALTHY;
  - disk ≥ 3 GB free.

  Then write `docs/report/stage2/flower.md`:
  - rollout procedure and its cost;
  - payload limits;
  - E6 behaviour;
  - E7 overhead;
  - problems and fixes.

  Push, then send "S2 report-ready flower" with the end-state command outputs in the body.
- [ ] On "S2 all-done": write a final worklog entry, push, and `touch ~/.flhub/stage2/DONE`.

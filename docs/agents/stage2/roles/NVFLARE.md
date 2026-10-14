# Stage 2 role: NVFLARE — NVFlare adapter + hospitals (145.38.189.142)

Through all of stage 2 you:

- keep `nvf-h1` and `nvf-h2` running standard NVFlare clients;
- extend the NVFlare adapter to flhub/0.2;
- roll out `flhub_core` 0.2 the NVFlare way;
- find NVFlare's payload limits;
- provide fault-injection tools on your VM;
- deliver the NVFlare native FedAvg baseline (E7).

Central runs every final run; you have no shell on central, so anything that must run there goes through a committed script plus a deploy-request.

## S0 — Kickoff

- [ ] Read PLAN.md, EXPERIMENTS.md and your stage-1 worklog tail. Create `docs/agents/worklogs/stage2-nvflare.md`.
- [ ] `deploy/nvflare-vm/status.sh`: both clients are connected to central. Clean the disk (PLAN.md §7). Post an `info` with the health result and free disk.

## S1 — Preparation (in parallel with central; do not touch the central deployment)

- [ ] **Payload research** (scout, against the installed NVFlare 2.9.0 source):
  - the maximum message size and streaming mechanism for large models;
  - the related config files (server and client side);
  - memory behaviour with large `FLModel` params.

  Write it in your worklog as "knob, file, side (server/hospital), default". Prepare the server-side knobs for S2.
- [ ] **Adapter: flhub/0.2** (PLAN.md §4.4), once central has specified it. Test it on an island:
  - per-site progress, partial collect, per-site cancel, then an immediate next dispatch;
  - readiness per run (registered but offline sites like `nvf-h7`/`nvf-h8` do not block);
  - payload cap ≥ 1024 MB;
  - relay job resubmitted automatically after an NVFlare server restart.
- [ ] **Rollout plan** for `flhub_core` 0.2. Preferred: ship it inside the job's custom code so hospitals need no manual step. Check how the client resolves imports: job custom dir versus the venv-installed `flhub_core` 0.1. ADR.
- [ ] **E6 tools on your VM** (EXPERIMENTS.md E6): `deploy/nvflare-vm/inject.sh` (arm/disarm; kill or restart a site at round N + S seconds, triggered by the local client log) and `deploy/nvflare-vm/straggler.sh` (CPU-throttle one site). Test both on your island.
- [ ] **E7 native baseline:**
  - NVFlare built-in FedAvg (controller or recipe) with the same `clients/nvflare` client script and aggregation weights = `num_examples`;
  - packaged as `deploy/central/nvflare/native/run.sh <config> <out_dir>`, writing `metrics.jsonl` in the hub's format (per-round global hash + checkpoints);
  - dry-run on your island, and compare its round-1 weights with a hub round.
- [ ] **E5 scripts:** add `nvf-h7`/`nvf-h8` to the provisioning without invalidating the existing kits (dynamic or incremental provisioning; check NVFlare's support with scout), plus start and stop for the extra clients.
- [ ] `deploy/central/nvflare/purge.sh` removes old job workspaces and run data on central (for E4a).
- [ ] `deploy/nvflare-vm/netem.sh` (PLAN.md §8): egress on your VM toward 145.38.205.58:8002 only; revert timer mandatory. Test it with a 1-minute profile under a RUNLOCK, then clear it.
- [ ] Start `scripts/sysmon.py` as soon as central pushes it; keep it running through S4.

## S2 — Rollout (milestone S2 S2-nvflare, announced by central)

- [ ] Provision all stage-2 data variants for `nvf-h1`/`nvf-h2` (later h7/h8), sha256-verified against the manifests. Switch the clients' data path to the directory if the data-variant design needs it (config only; ledger row).
- [ ] Send a deploy-request for the central-side changes:
  - new job custom code;
  - adapter flhub/0.2;
  - payload knobs;
  - `native/` and `purge.sh`.

  After deployment:
  - selfcheck v2 inside the client runtime (log the code_version and hashes);
  - conformance v2 passes against central (central runs it).
- [ ] Ledger rows for every hospital-side step. Measure rollout time, steps, client restarts and downtime for the RQ2 comparison.
- [ ] `git diff --stat stage1-final..HEAD -- clients/nvflare` goes in your worklog; explain any non-zero line.

## S3 / S4

- [ ] Confirm the S3-frozen SHA (PLAN.md §5): the empty diff and the frozen content-hash code_version reported by your hospitals.
- [ ] Keep the clients up and watch the logs during every run. Report anomalies immediately (`blocker` to central with log lines).
- [ ] For every "RUN end" from central, send the sysmon JSON summary of that window (`tools/bus put <file> --to central`).
- [ ] **E4a:** if a size fails, propose the server-side knob from your research (deploy-request, ADR) for one retry; then record the limit.
- [ ] **E4b:** apply and clear your netem profile at the times central requests. Confirm both on the bus.
- [ ] **E6:** answer every ARM request: arm `inject.sh` or `straggler.sh`, reply "ARMED", confirm the outcome, the watchdog recovery and HEALTHY status after the run, then disarm.
- [ ] **E5:** start `nvf-h7`/`nvf-h8` when central asks; stop them afterwards.
- [ ] **E7:** help central run `native/run.sh`; then analyse native vs hub (weight difference, overhead) for your report section.

## S5

- [ ] On "S2 final-call", first the end state on your VM:
  - netem cleared (`tc qdisc show`);
  - no `flhub-netem*` timers (`systemctl list-timers 'flhub-netem*'`);
  - injection and straggler tools disarmed or off;
  - extra sites stopped;
  - base clients HEALTHY;
  - disk ≥ 3 GB free.

  Then write `docs/report/stage2/nvflare.md` (per-agent section of the stage-2 report template):
  - rollout procedure and its cost;
  - payload limits and knobs;
  - E6 behaviour;
  - E7 overhead analysis;
  - problems and fixes.

  Push, then send "S2 report-ready nvflare" with the end-state command outputs in the body.
- [ ] On "S2 all-done": write a final worklog entry, push, and `touch ~/.flhub/stage2/DONE`.

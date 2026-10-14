# Stage 2 role: VANTAGE6 — vantage6 adapter + hospitals (145.38.186.63)

Through all of stage 2 you:

- keep `v6-h3` and `v6-h4` running standard vantage6 nodes;
- extend the vantage6 adapter to flhub/0.2;
- roll out `flhub_core` 0.2 the vantage6 way (a new algorithm image);
- find vantage6's payload limits;
- provide fault-injection tools on your VM;
- deliver the vantage6 native FedAvg baseline (E7).

Central runs every final run; you have no shell on central, so anything that must run there goes through a committed script plus a deploy-request.

## S0 — Kickoff

- [ ] Read PLAN.md, EXPERIMENTS.md and your stage-1 worklog tail. Create `docs/agents/worklogs/stage2-vantage6.md`.
- [ ] `deploy/vantage6-vm/status.sh`: both nodes are connected to central and the watchdog cron is active. Clean the disk: keep only the current algorithm image plus base images (PLAN.md §7). Post an `info` with the health result and free disk.

## S1 — Preparation (in parallel with central; do not touch the central deployment)

- [ ] **Payload research** (scout, against the installed vantage6 4.15.2 source):
  - where task input and results are stored (server DB column types, JSON encoding, base64 inflation);
  - request size limits (web server, socket events, node ↔ server upload/download);
  - node and container memory behaviour.

  Write it in your worklog as "knob, file, side (server/hospital), default". Large payloads are the likeliest vantage6 limit: find the boundary before E4a, and prepare the server-side knobs for S2.
- [ ] **Adapter: flhub/0.2** (PLAN.md §4.4), once central has specified it. Test it on an island:
  - per-site progress, partial collect, per-site cancel (kill the remaining runs), then an immediate next dispatch;
  - readiness per run (registered but offline orgs like `v6-h9`/`v6-h10` do not block);
  - payload cap ≥ 1024 MB;
  - automatic recovery after a vantage6 server restart.
- [ ] **Data variants:** decide how a node offers several partition files, for example several labelled databases in the node config (one node restart, config only) or the lazy-directory design from central. ADR. The adapter passes the label or variant per task.
- [ ] **Rollout plan** for `flhub_core` 0.2: a new image tag pushed to the central registry, referenced by the adapter per task (the vantage6-standard way; hospitals pull automatically). Pre-pull on the nodes to keep round 1 fast.
- [ ] **E6 tools on your VM** (EXPERIMENTS.md E6): `deploy/vantage6-vm/inject.sh` (arm/disarm; kill a node's running algorithm container, or restart the node, at round N + S seconds, triggered by the node log) and `deploy/vantage6-vm/straggler.sh` (CPU-throttle the algorithm containers of one node only, for example a cgroup parent per node or `docker update --cpus`; ADR). Test both on your island.
- [ ] **E7 native baseline:**
  - idiomatic vantage6 FedAvg: a central function that creates partial tasks and aggregates by `num_examples`, with the same `flhub_core.train`;
  - packaged as `deploy/central/vantage6/native/run.sh <config> <out_dir>`, writing `metrics.jsonl` in the hub's format;
  - decide where its central part runs on central and what that needs (an extra org or node, or the client on central); ADR;
  - dry-run on your island.
- [ ] **E5 scripts:** orgs and nodes `v6-h9`/`v6-h10` (central creates them and sends API keys via `--secret`), plus start and stop for the extra nodes and their watchdog entries.
- [ ] `deploy/central/vantage6/purge.sh` deletes old tasks, runs and results from the server DB (for E4a disk safety).
- [ ] `deploy/vantage6-vm/netem.sh` (PLAN.md §8): egress toward 145.38.205.58 ports 7601 and 5050 only; revert timer mandatory. Test it with a 1-minute profile under a RUNLOCK, then clear it.
- [ ] Start `scripts/sysmon.py` as soon as central pushes it; keep it running through S4.

## S2 — Rollout (milestone S2 S2-vantage6, announced by central)

- [ ] Build the new algorithm image (`clients/vantage6` + `flhub_core` 0.2) and run selfcheck v2 inside it with `--network none`.
- [ ] Send a deploy-request for:
  - the image push to `localhost:5050` on central;
  - adapter flhub/0.2 using the new tag;
  - server knobs;
  - `native/` and `purge.sh`.
- [ ] Provision the data variants on the nodes and apply the chosen data-variant mechanism (ledger rows).
- [ ] Conformance v2 passes against central (central runs it). Measure rollout time, steps, node restarts and downtime for the RQ2 comparison.
- [ ] `git diff --stat stage1-final..HEAD -- clients/vantage6` goes in your worklog; explain any non-zero line.

## S3 / S4

- [ ] Confirm the S3-frozen SHA (PLAN.md §5): the image tag in use contains the frozen content-hash code_version, the diff is empty, and `status.sh` is HEALTHY.
- [ ] Watch node and algorithm-container logs during every run. Report anomalies immediately.
- [ ] For every "RUN end" from central, send the sysmon JSON summary of that window (`tools/bus put <file> --to central`).
- [ ] **E4a:** if a size fails, propose a server-side knob (deploy-request, ADR) for one retry, then record the limit with the evidence:
  - error text;
  - which component fails;
  - the DB, request or memory size at the failure.
- [ ] **E4b:** apply and clear your netem profile at the times central requests. Confirm both on the bus.
- [ ] **E6:** answer every ARM request: arm, reply "ARMED", confirm the outcome and the watchdog recovery after the run, then disarm.
- [ ] **E5:** start `v6-h9`/`v6-h10` when central asks; stop them afterwards and disable their watchdog entries.
- [ ] **E7:** help central run `native/run.sh`; analyse native vs hub for your report section.

## S5

- [ ] On "S2 final-call", first the end state on your VM:
  - netem cleared;
  - no `flhub-netem*` timers;
  - injection and straggler tools off;
  - extra nodes stopped and their cron entries removed;
  - old images pruned;
  - base nodes HEALTHY;
  - disk ≥ 3 GB free.

  Then write `docs/report/stage2/vantage6.md`:
  - rollout procedure and its cost;
  - data-variant mechanism;
  - payload limits;
  - E6 behaviour;
  - E7 overhead;
  - problems and fixes.

  Push, then send "S2 report-ready vantage6" with the end-state command outputs in the body.
- [ ] On "S2 all-done": write a final worklog entry, push, and `touch ~/.flhub/stage2/DONE`.

# Stage 2 — central agent section

## 1. Scope

The hub side of stage 2 (PLAN.md §4.1–§4.3, roles/CENTRAL.md): the protocol extension flhub/0.2 and its reference implementation in `adapters/common`; `flhub_core` 0.2 (dataset, partitions, data variants, model zoo, training options, selfcheck v2); the stateful aggregators; orchestrator and sim v2; the run configs, the S3 tuning and the freeze; every final run (real, sim, reference, native), its INV-1 gate and its W&B upload; the central-side tools (sysmon, netem, E6 injector, E4a sweep, campaign driver, post-processing worker); `REPORT_STAGE2.md`.

## 2. What changed

| area | files | ADR |
|---|---|---|
| protocol flhub/0.2 | `docs/agents/PROTOCOL.md` §7, `adapters/common/{tasks,base,relay,server,hubclient,rpcclient,envelope,protocol}.py`, `adapters/mock`, `hub/core/server.py`, `tests/conformance` (v2: 13 tests) | ADR-008 |
| shared library | `flhub_core/src/flhub_core/{data,partition,model,train,selfcheck,__init__}.py`, `configs/stage2/partitions/*.json` | ADR-009, ADR-010 |
| aggregators | `hub/aggregators/` | — (PLAN §4.1) |
| orchestrator / sim | `hub/orchestrator/{config,participation,evaluation,checkpoint,inv1,executors,run,__main__}.py`, `hub/sim/{__main__,local}.py` | — (PLAN §4.3) |
| configs | `configs/stage2/{base,tuned,topology}.yaml` + 98 run configs (generated) | — |
| campaign tools | `scripts/stage2/*.py`, `scripts/sysmon.py`, `deploy/central/netem.sh` | — |

*(pending: git diff --stat stage1-final..HEAD for these directories at the end of S4)*

## 3. Rollout (central side)

Central ran the framework agents' deploy-requests in arrival order: NVFlare #141 (+ keeper fix, relay timings), Flower #144 (+ #177, #205), vantage6 #174 (+ adapter at 890966d), and E5 credentials (NVFlare kits bus #4/#5, vantage6 node keys bus #6). Conformance v2 against the central deployment: 13/13 for all three adapters, every weight difference 0.0 (`docs/report/stage2/evidence/conformance-v2-*-central.json`).

## 4. Limits and knobs (central side)

- `RpcServer` and the hub core accepted bodies up to 512 MiB in stage 1; flhub/0.2 raised this to 2048 MiB and streams blobs to and from disk (530 MiB round trip: put 1.4 s, get 1.2 s).
- The central VM's 19 GB disk was the binding limit of E4a (testbed limit, PLAN §7): see `REPORT_STAGE2.md` §6.

## 5. Experiments run

All final runs: `docs/report/stage2/runs.md` (W&B project `AgnosticFL-stage2`). *(pending: summary after S4)*

## 6. Problems and fixes

| problem | symptom | root cause | fix | SHA | time lost |
|---|---|---|---|---|---|
| aggregator state hash not reproducible | `state_ref({})` differed between two calls | safetensors keeps metadata in a hash map; two metadata keys serialize in varying order | one metadata key holding sorted JSON | 20f9421 (pre-push) | 5 min |
| commit size hook | pushes rejected (418 / 436 lines) | commits over the 400-line limit | split commits | — | 5 min |
| stale hub core | 512 MB blobs would have been refused with HTTP 413 in E4a | the hub-core systemd service still ran stage-1 code (started 12:27Z) | restarted on the frozen code before the 256/512 MB sizes; lesson: restart every central service after a change in its package | — | 2 min |
| adapters read the global model needlessly | +1.46 s per round and ~1 GB adapter RSS at 512 MB (found by the flower agent, #173) | `BaseAdapter._work` deserialized the model for every task, relays never use it | `needs_weights` flag (False for relays and vantage6) | a43cef3 | 10 min |
| disk at 512 MB and vantage6 256 MB | E4a sizes stopped | 5-6 GB free on central; vantage6 sqlite + journal grow ~1.3 GB per 128 MB of payload | drop payload checkpoints after INV-1 (hashes suffice), measured disk projections in the pre-run check; owner removed the local stage-1 image copy (+1.5 GB) | — | — |
| E1 reference jobs failed at start | KeyError 'pooled'; BrokenProcessPool | partition sizes indexed with the centralized pseudo-site; the local-only worker function lived in `__main__` (spawned workers cannot import it) | 1-line fix + `hub/sim/local.py`, re-freeze 975a949 (#396) | 975a949 | 15 min |
| a wait loop never ended | background `pgrep -f` loop | the pattern matched the loop's own command line (stage-1 lesson) | wait on a PID file | — | 5 min |
| frozen SHA recorded before the push | frozen_sha pointed to a pre-rebase SHA | `git pull --rebase` rewrote the local commit | write the frozen SHA after the push | — | 1 min |
| vantage6 store between experiments | 1.1 GB server DB after E2 (750 tasks of a 209 KB model) | every run row keeps input and result in double base64 (1.78× the payload each) | `purge.sh --vacuum` between experiments and before every E3 run; models ≥ 16 MB are deleted per round by the adapter (ADR-020) | 4c6ab69 | 15 min (a first purge call hit the 10 min tool limit mid-delete; rerun finished in 1.3 min) |
| E3 checkpoints would pile up | four E3 run dirs before finalize would leave < 3 GB free | finalize applies retention, but sims are not allowed during E3 runs | the E3 driver finalizes each run (INV-1 sim between runs) before the next | 4c6ab69 | — |
| sysmon summaries of INV-4 re-runs filed under the original runs | `inv4-<run>` files matched by substring | the collector matched names by substring | exact `sysmon-<fw>-<run>` / `<run>-<fw>-sysmon` names | bc5d271 | 2 min |
| empty stray files in the repo root | 13 zero-byte files named `PASS`, `Done`, `7`, ... (14:03Z) | a mis-quoted shell redirect in an earlier command | removed; never committed (`git status` before every commit) | — | 1 min |

## 7. Recommendations

- **Gate before upload.** Creating the W&B run only after the INV-1 gate passed (`finalize_run.py`) kept the project clean without a single deletion, and made a SIGKILL of the hub (E6d) harmless for the experiment tracker.
- **Budget the framework's own store, not just the payload.** vantage6 keeps every task's input and result in its server database; a 45 MB model costs about 320 MB per round on a 2-site run unless tasks are deleted after collection. Plan disk per framework from a measured run (here `disk_projection.json`), not from the payload size.
- **Restart a service after updating its package.** The hub core still ran stage-1 code hours after the stage-2 rollout and would have refused 512 MB blobs.
- **Keep timing runs free of other CPU load.** A sim with 4 workers on the 4-vCPU central VM changes round times; the post-processing queue (nice 19, at most 2 workers during learning runs, none during timing runs) made that rule automatic.
- **Tune server learning rates per participation level.** The best aggregator with all sites (FedAvgM, η 1.0) diverged with half of the sites per round (E6c); η 0.3 was stable.
- **For large models, replay instead of full simulation.** Recomputing 4 rounds from the real run's own checkpoints proves INV-1 at a fraction of the cost of re-training every round.
- **Make every handshake a bus message with a fixed subject** (RUNLOCK, ARM/ARMED, RUN start/end with round windows, NETEM, E5 EXTRAS): scripts on both sides could wait for each other, and the transcript is the evidence.

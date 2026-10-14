# Stage 2 — Flower agent (145.38.187.20)

Status: draft during S2–S4; experiment results are filled in from the final runs (`runs.md`) in S5.

## 1. Scope

- Hospitals `flw-h5`, `flw-h6` (standard `flower-supernode`, Flower 1.39.0) on the flower VM. E5 extra sites `flw-h11`, `flw-h12` are started by `deploy/flower-vm/extra.sh` only for E5.
- Flower adapter (`adapters/flower/service.py`) on flhub/0.2, the relay ServerApp (`clients/flower/flhub_flower/server_app.py`, runs only on central) and the central scripts (`deploy/central/flower/`).
- Rollout of `flhub_core` 0.2 through the Flower FAB channel, payload limits (E4), fault-injection tools on the VM (E6), the native FedAvg baseline (E7).

## 2. What changed

| area | change | ADR |
|---|---|---|
| adapter | flhub/0.2 on `adapters/common` `RelayAdapter`. Stages `clients/flower` and `flhub_core` into one FAB source directory and submits it with `flwr run` (`fab-id` in the run config, checked through the relay beat). Adds `/relay/beat`. 20 s without poll, progress or beat means the relay is lost: its rounds fail at once and a new relay run starts (retry 10 s, settle 60 s). Sites are `unknown` without a live relay. `max_payload_mb` 2048. Registers `flw-h11`, `flw-h12` (offline until E5). | ADR-016 |
| relay | per-site results and errors are posted as replies arrive (`post_site_result`, `post_site_error`). Polls `/relay/job` every 5 s and drops sites the hub no longer waits for; late replies are pulled, discarded and logged. Sends to available sites at once. Unmapped sites get at most min(60 s, deadline/4). A SuperNode replaced by a restart counts as lost within about 5 s of its site-info answer. Per-site timings `flower_relay_{prep,push,reply,store}_s` go into the result metrics (66e806e). | ADR-016 |
| FAB metadata | `clients/flower/pyproject.toml` version 0.1.0 → 0.2.0 and the `fab-id` config key. | ADR-016 |
| hospitals | `flhub_core` 0.1.0 in the hospital venv is pinned to a fixed copy of `stage1-final` (it was an editable link to the VM checkout). Data variants are provisioned next to `<site>.csv`. `start.sh` accepts variant-only sites. | ledger |
| E6 | `deploy/flower-vm/inject.sh` (log-triggered kill, kill-node, restart, straggler-on, straggler-off), `straggler.sh` (user systemd scope with `CPUQuota`; `prepare`, `on`, `off`, `release`), `deploy/central/flower/restart_server.sh` (SuperLink only), `restart.sh adapter|superlink`. | ADR-017 |
| E7 | `deploy/central/flower/native/`: `flhub_native` ServerApp (built-in `flwr.serverapp.strategy.FedAvg` with hooks), `run.sh`, `compare.py`. | — |
| E4/E5/ops | `deploy/central/flower/purge.sh` (stop, WAL checkpoint and VACUUM, start), `deploy/flower-vm/netem.sh`, `sysmon.sh`, `extra.sh`, `island.sh`. | — |

`git diff --stat stage1-final..HEAD -- clients/flower` (at 66e806e): `flhub_flower/server_app.py` 291 lines (the relay ServerApp, runs only on central), `pyproject.toml` 4 lines (FAB version, `fab-id`). `client_app.py` and `records.py`, the code the hospitals execute, have 0 changed lines. Ledger rows: `docs/report/stage2/ledger.md` (flower).

## 3. Rollout of flhub_core 0.2

Channel: the FAB (Flower App Bundle). A FAB holds the app directory's `*.py`, `*.toml`, `*.md`, `*.yaml`, `*.yml`, `*.json` and `*.jsonl` files (at most 10 MiB; `test_*.py`, `lib/`, `build/`, `__pycache__` and symlinks are skipped). Every message starts a fresh `flwr-clientapp`. It unpacks the run's FAB into `$FLWR_HOME/apps/<publisher>.<name>.<version>.<hash8>` and calls `sys.path.insert(0, app_dir)` before it imports the ClientApp (`supercore/object_ref.py:182`). A `flhub_core/` inside the FAB therefore takes precedence over the venv copy. Connected SuperNodes fetch a new run's FAB by hash, with no restart. Runtime dependency installation stays off, so FAB dependencies are never installed.

Island proof: SuperNodes with `flhub_core` 0.1.0 in the hospital venv reported the FAB's marker version `0.1.99+29638ceb7307`. On the real system they report `0.2.0+fe357a1dbec1`.

Procedure on the real system:

| step | who | what | wall time | client restarts | downtime |
|---|---|---|---|---|---|
| 0 | hospital (stage-2 prep) | pin stage-1 `flhub_core` as a fixed copy (`install.sh`) | seconds | 0 | 0 |
| 1 | hospital | provision data variants next to `<site>.csv`, sha256-checked against the manifests (`python -m flhub_core.data materialize --variants all --sites flw-h5 flw-h6 flw-h11 flw-h12 --manifests configs/stage2/partitions`): 258 MB | 44 s | 0 | 0 |
| 2 | central | `git pull`; `bash deploy/central/flower/restart.sh adapter` (SIGTERM at 15:10:29.2Z, healthy at 15:10:58.9Z) | 29.6 s | 0 SuperNode restarts | 0 for hospitals (no relay for about 30 s on the server side) |
| 3 | hospitals (automatic) | both SuperNodes fetched and unpacked `agnosticfl.flhub-flower.0.2.0.3733e3db` at 15:10:50Z, with the new relay's first message | +21.5 s | 0 | 0 |

Selfcheck v2 in the hospital runtime (hospital venv Python with the FAB first on `sys.path`): `flw-h5` and `flw-h6` report digest `sha256:ac074a0b79a2d3c6782765480164c0465ac598359dfe91695e893e208f4e5034`, equal to central.

Conformance v2 against central: 13 checks passed, `max_abs_diff` 0.0 in every round. Checks: subset dispatch (r5), partial collect and site cancel (r6), then the next dispatch (r7), offline extras (r8), adapter restart (r10, online after 35 s), `payload-64` echo (r11), `organamnist-dir0.3` (r12); n = 5760. Evidence: `docs/report/stage2/evidence/conformance-v2-flower-central.json`. Milestone `S2 S2-flower` (bus #154).

What a real hospital does: runs the standard SuperNode with its node config (`site-id`, data path), with torch, numpy and safetensors installed. Each later `flhub_core` version arrives in the FAB of the next run. No hospital action and no restart. For comparison, NVFlare appends `custom/` to `sys.path`; there the hospitals had to remove the venv copy once and restart their clients.

## 4. Limits and knobs (E4)

| knob | file | side | default |
|---|---|---|---|
| gRPC max message size | `flwr/supercore/grpc.py:30` (`GRPC_MAX_MESSAGE_LENGTH`) | SuperLink and SuperNode | 2147483647 B (2 GiB), not configurable |
| array chunk size | `common/constant.py:161-163` (`FLWR_PRIVATE_MAX_ARRAY_CHUNK_SIZE`, env) | sender of a message | 5 MiB |
| concurrent object pushes and pulls | `supercore/constant.py:39-43` (`FLWR_PRIVATE_MAX_CONCURRENT_OBJ_PUSHES/PULLS`, env) | each side | 2 |
| FAB size | `common/constant.py:64` | `flwr run` | 10 MiB |
| SuperLink HTTP API | FastAPI/uvicorn; no body limit found | SuperLink | — |
| message TTL | `app/constants.py:18` | ServerApp, per message | 12 h (the relay uses the round deadline) |
| heartbeat | `common/constant.py:44,51` | SuperNode beats about every 20 s; offline after 60 s | — |
| SuperLink state | `--database` SQLite; message objects in table `objects`, ref-counted delete, freed pages kept (no VACUUM) | SuperLink | — |

Island rehearsal (whole path on the flower VM, `configs/stage2/e4a/s2-e4a-flower-<S>mb.yaml`, 3 rounds, purge between sizes). All sizes passed without a knob:

| payload | round time |
|---|---|
| 64 MB | 8.1–9.5 s |
| 128 MB | 13.9–14.4 s |
| 256 MB | 22.6–25.4 s |
| 512 MB | 56.6 / 45.5 / 47.5 s |

Peak RSS at 512 MB: orchestrator 4.2 GB, relay 3.2 GB, SuperLink 1.1 GB, adapter 1.0 GB, two ClientApps 4.9 GB, SuperNodes 2.5 GB. After a run the SuperLink DB and WAL reach about 4 × payload (2082 MiB at 512 MB). `purge.sh` brings them to about 1 MB in about 35 s.

Breakdown at 128 MB (relay timings, island), per site of 12.4–14.1 s: prep 0.2 s; push 1.6–2.0 s; push to reply 9.4–11.2 s (SuperNode download, ClientApp start about 3 s, echo, upload); store 0.5 s; train 0.05 s.

Found in `adapters/common` (central's code): `BaseAdapter._work` downloaded the global model for every task, although `RelayAdapter` ignores it. Cost: +1.46 s per round and about 1 GB adapter RSS at 512 MB. Reported on the bus (#173); fixed by central in a43cef3 (`needs_weights = False` for relay adapters) before S3-frozen.

E4a on the real system (central runs `s2-e4a-flower-<S>mb`, 3 rounds, `purge.sh` before every size; medians of rounds 2–3 from the RUN end messages; flower VM memory peak from sysmon):

| payload | median round | flower VM memory peak | result |
|---|---|---|---|
| 1 MB | 5.79 s | 1.9 GB | finished |
| 8 MB | 6.07 s | 2.0 GB | finished |
| 32 MB | 7.69 s | 2.1 GB | finished |
| 64 MB | 9.08 s | 2.5 GB | finished |
| 128 MB | 13.51 s | 3.6 GB | finished |
| 256 MB | 20.61 s | 4.9 GB (16 %) | finished |
| 512 MB | — | — | testbed disk limit on central (7.0 GB free needed, 6.0 GB available); passed on the island |

Where the time goes (`deploy/flower-vm/analysis/e4a_breakdown.py` on the run dirs central sent, bus files #74/#75; medians over rounds 2–3 and both sites; relay timings from 66e806e):

| MB | round | t_result | relay prep | push to SuperLink | Flower transport (reply − train − load) | store in hub core | train |
|---|---|---|---|---|---|---|---|
| 1 | 5.79 s | 5.28 s | 0.00 s | 0.07 s | 5.11 s | 0.01 s | 0.00 s |
| 8 | 6.07 | 5.04 | 0.02 | 0.14 | 4.69 | 0.03 | 0.00 |
| 32 | 7.69 | 6.29 | 0.09 | 0.47 | 5.47 | 0.14 | 0.01 |
| 64 | 9.08 | 7.93 | 0.17 | 0.82 | 6.46 | 0.27 | 0.03 |
| 128 | 13.51 | 12.58 | 0.24 | 1.61 | 10.03 | 0.53 | 0.05 |
| 256 | 20.61 | 17.75 | 0.37 | 3.35 | 12.75 | 1.06 | 0.10 |

Flower's own path costs about 5 s per round at any size (a fresh `flwr-clientapp` process per message plus pull intervals) and about 0.03 s per MB on top (SuperLink → SuperNode download, upload, relay pull; 5 MiB chunks, 2 concurrent transfers, SQLite object store). The hub side (relay prep, push, store, adapter) adds 0.1 s at 1 MB and 4.9 s at 256 MB. In the mixed scope Flower's fan-out stays at 5.3–8.6 s (1–64 MB) while the round is set by the slowest framework.

No server-side knob was needed. The SuperNodes logged a few `Connection attempt failed, retrying` lines at the start of the 64 MB and 256 MB runs: reconnects after `purge.sh` restarted the SuperLink just before the run (same node ids, all rounds finished).

E4b on the real system: (S4 results).

## 5. Experiments supported

- E6 tools, island, 4 rounds, `round_retries` 2. Final weights of every run equal the clean run, `sha256:f5c8f670...`.
  - `kill-node` at round 2 + 3 s: SuperNode back after 11.1 s; relay failed the site 26 s after the kill (replaced node); retry succeeded.
  - `kill` (ClientApp only): Flower's own `CLIENT_APP_CRASHED` reply ("ClientApp stopped responding") after 58 s (task heartbeat expiry); retry succeeded.
  - `restart` (SIGTERM): `NODE_UNAVAILABLE` 1.2 s later (node deregisters); back after 11.6 s.
  - Straggler at 10 % CPU: 43–50 s per round against 8 s, same weights. A SuperNode started at 10 % takes about 20 s, so `straggler.sh prepare` runs before the run and `on` is armed at the round.
- E6a timing (`dir0.3`, `cnn-small`, this VM): ClientApp starts 0.3 s after the train message; data load 0.17 s; training 1.6 s at about +3.2 to +4.8 s; process ends at +5.0 s. The kill-node injection is armed with `--after 4`.
- E6e (SuperLink SIGKILL): the SuperLink kill also kills superexec and the relay ServerApp (parent-pid monitors). The adapter detects the lost relay after 20 s, fails the rounds the relay had taken at once and relaunches. Island: healed in 61 s (SuperLink up after about 2 s). Earlier test: round 3 of 8 failed 25 s after the kill, was retried, and the run finished 8/8.
- E6d (adapter SIGKILL): `restart.sh adapter` brings the adapter up 1.5 s later; the live relay re-attaches (same run id); healthy after 2.7 s.
- E7 native (island dry runs, `configs/stage2/e7/s2-e7-flower-hub-{cnn,payload}.yaml`): built-in FedAvg with `fraction_train` 1.0, no federated evaluation, weighted by `num-examples`. Hooks: `round` is added to the config (flhub_core seeds the data order with it; FedAvg itself adds only `server-round`); the Grid the strategy sees lists one SuperNode per site; hub-format checkpoints and evaluation via `evaluate_fn`. Strict: all sites must reply. Runs as its own Flower run next to the idle relay run (no pause). Native equals hub bit-exactly in every round (n 5760/5760 and 784/784, so p_k = 0.5 exactly in float32). Median round time, native / hub: cnn 9.48 / 7.80 s; payload-45 7.40 / 9.16 s. `Grid.send_and_receive` sleeps 3 s between pulls, so native rounds are quantised to 3 s.
- E7 precision check (island, `qty` partition, p_k = 2194/6583 and 4389/6583, not exact): max abs diff 3.0e-8 after round 1 (float32 against float64 aggregation), 1e-6 to 7e-6 in rounds 2–4, about 2e-2 from round 6 (SGD amplifies). Accuracy equal to round 4, then within 0.6 pp.
- netem test (RUNLOCK #132): RTT to port 9092 went from 0.77 ms to 20.89 ms. Bus port 7700 unchanged; SuperNodes stayed connected. Cleared; default `fq_codel`; 0 timers left.
- E6 final runs, flower side (hospital VM evidence; detection and recovery times on the hub side are in central's E6 table):
  - `s2-e6a-strict` and `s2-e6a-minsites`: `inject.sh` killed the flw-h5 SuperNode tree (SuperNode, SuperExec, ClientApp) 4 s after round 15's train message (21:29:00.75Z and 21:34:38.48Z); the supervisor logged `exited rc=137, restarting in 10 s` and the SuperNode registered again after 11.1 s both times (new node id). Both runs finished 20/20; no manual action.
  - `s2-e6b-straggler`: flw-h6 was moved into its CPU scope before the run (`straggler.sh prepare`); the quota went to 10 % 40 ms after round 14's train message reached flw-h6. The throttled ClientApps finished after the 20 s deadline; their reply pushes failed at the SuperNode (`NoObjectInStoreError`, `Failed to push reply message`) because the messages had expired (TTL = deadline), so Flower dropped the late results on the hospital side; the SuperNode kept running. The limit stayed on for the first 17 s of `s2-e6c-fedavg` (central started it right after the RUN end); weights are unaffected, only that run's warm-up timing at flw-h6.
  - `s2-e6e-server-restart`: central restarted the SuperLink after round 15 and in the middle of round 18; neither SuperNode restarted, both kept their node ids (persistent `--database`), flw-h6 logged three reconnect retries; the run finished 20/20 with no manual action.
- E7 final runs (central ran `deploy/central/flower/native/run.sh` with `configs/stage2/e7/s2-e7-flower-hub-{cnn,payload}.yaml` and the hub runs of the same configs; `native/compare.py` and central's `native_vs_hub.json` on the run dirs, bus file #220):

| config | native median round | hub median round | hub overhead | native vs hub weights |
|---|---|---|---|---|
| C1 `cnn-small`, `iid`, 20 rounds | 9.48 s | 7.55 s | −1.93 s (−20 %) | hash-equal in all 20 rounds (max abs diff 0.0) |
| C2 `payload-45`, 5 rounds | 7.45 s | 8.19 s | +0.73 s (+9.8 %) | hash-equal in all 5 rounds |

  - Equality: both Flower sites hold the same number of examples (5760 for `iid`, 784 for the stage-1 data used by the echo), so p_k = 0.5; Flower's FedAvg multiplies in float32, but 0.5 × w is exact and both paths round the same exact sum once. With unequal sizes the float32 accumulation differs from the hub's float64 one (island `qty`: 3.0e-8 after round 1, growing to about 2e-2 by round 6 through SGD).
  - Timing: for the CNN the hub path is faster, because `Grid.send_and_receive` (the built-in strategy's exchange) sleeps 3 s between pulls, which quantises native rounds to about 9.5 s; the relay pulls every second. For the 45 MB echo the hub path pays the weights' round trip through the hub core (per site: relay prep 0.12 s, push 0.63 s, store 0.19 s; push → reply 6.15 s; t_result 7.18 s).
  - Flower's own accounting of the native runs (`flwr ls --format json`): 17.2 MB on the wire for C1 (20 rounds × 2 sites × 2 × 209 KB = 16.7 MB) and 943.9 MB for C2 (= 20 × 47.2 MB), compute 237 s ServerApp + 125 s ClientApps for C1.
  - The native run ran next to the idle relay run on the same SuperLink; nothing was paused, and the hospitals received the native FAB (`agnosticfl.flhub-native.0.2.0.82153140`) automatically with its first message.
- E5 (flower side): `flw-h11` and `flw-h12` started with `extra.sh start` on `E5 EXTRAS UP` (06:08:01Z), connected and mapped by the relay 12 s later, trained all 20 rounds of `s2-e5-12sites`, and were stopped with their watchdog entry on `E5 EXTRAS DOWN` (06:13:22Z).
- Final-run results (E4b, E4a 512 MB extension): (S4 results).

## 6. Problems and fixes

| problem | symptom | root cause | fix | SHA | time lost |
|---|---|---|---|---|---|
| hospital code followed the VM checkout | `__editable__.flhub_core` in the hospital venv | stage-1 `install.sh` used `-e flhub_core` | fixed copy of `stage1-final`; 0.2 arrives through the FAB | ac0578f | 5 min |
| relay died with the SuperLink; the round waited for its deadline | stage-1 adapter noticed after 45 s; the in-flight round failed only at `deadline_s` | Flower's parent-pid monitors end superexec and the relay ServerApp with the SuperLink; relay liveness was judged from polls only | relay beat thread (20 s stale), rounds taken by a lost relay fail at once, fast relaunch | acec9de | 30 min |
| false HEALTHY after a SuperLink kill | `status.sh` OK 2 s after the kill | cached site reports (90 s) and poll check (45 s) | sites `unknown` without a live relay; `restart.sh` waits for a new relay run | 6ba8981 | 10 min |
| `start.sh` reported "adapter started" for a dead adapter | port still held by the old adapter | `/health` answered by the old process | check the new pid after `/health` | 01a219e | 5 min |
| `node_port` returned 1 for a non-last site | `straggler.sh` aborted after stopping the site (island) | `set -e` with `x=$(f)` and a failing last command | `return 0` | 7d6d1b8 | 10 min |
| SuperLink DB stays at peak size | DB 135 MB with 99 % free pages and 138 MB WAL after payload-64 | SQLite keeps freed pages | `purge.sh` (VACUUM) between E4a sizes | 6ba8981 | — |
| ADR number collision | rebase conflict in `docs/DECISIONS.md` | parallel agents picked the same number | renumbered after pull (ADR-016, ADR-017) | de5d913 | 10 min |
| adapter restart on central took 97 s instead of ~30 s | an intermediate relay run (3704…) was no longer RUNNING when the SuperNodes got its site-info query | a stale-FAB relay was removed through the adapter's single global shutdown flag, which the new relay could take; the launcher then waited out its 60 s cooldown | the relay sends its fab id with every poll; only a stale relay gets `shutdown`, its beats get no liveness credit | 17cdfd1 | 20 min |
| cron watchdog inert since 12:52Z | `straggler.sh prepare` failed with "watchdog lock busy" | a SuperNode supervisor started by the cron watchdog under `flock -n` inherited the lock's file descriptor, so every later `flock -n` failed (the per-SuperNode restart loop kept working) | cron line `flock -n -o`; `straggler.sh` stops a lock-holding site first | (bus #592) | 10 min |
| straggler still on when the next run started | flw-h6 at 10 % for the first 17 s of `s2-e6c-fedavg` | central starts the next run right after RUN end; the straggler was switched off by hand | noted (timing only); a straggler window should end inside the run (vantage6 `--until-round`) | — | — |
| `pgrep` matched itself while measuring | timeline empty | pattern matched the sampler's own command line | anchored pattern | — | 5 min |

## 7. Recommendations

- Ship project libraries inside the FAB. Flower puts the FAB first on `sys.path`, and every message runs a fresh ClientApp process, so an update needs no hospital action and no restart. Keep runtime dependency installation off and keep the library within the FAB rules.
- Plan for the relay ServerApp dying with the SuperLink: Flower's parent-pid monitors end it together with the SuperLink, and nothing restarts it. An independent heartbeat detects the loss quickly; an external keeper must resubmit the run.
- For large models: the SuperLink stores message objects in SQLite (5 MiB chunks, 2 concurrent transfers). Budget about 4 × payload of disk per round and run VACUUM between runs. At 64 MB and above, transport dominates the round time.
- Built-in FedAvg aggregates in float32 in reply order. It is bit-equal to a float64 reference only when every p_k × w_k is exact (equal site sizes). The difference grows through later training. `Grid.send_and_receive` polls every 3 s.

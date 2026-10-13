# Stage 2 worklog — flower agent

Newest entries at the bottom. Format: `### <UTC timestamp> — S<n> — <title>` + 2–6 bullets (what, result, evidence, next).

### 2026-10-13T13:50Z — S0 — kickoff, health, disk
- Read PLAN.md, EXPERIMENTS.md, roles/FLOWER.md, stage-1 worklog tail; bus: STAGE2-START #98.
- `deploy/flower-vm/status.sh` exit 0: flw-h5 + flw-h6 connected to 145.38.205.58:9092, watchdog cron enabled.
- Disk: 14G free of 19G (4.6G used); `uv cache prune` found nothing unused. Posted info #100.
- Hospital venv `~/venvs/flower`: flwr 1.39.0, torch 2.14.1+cpu, flhub_core 0.1.0 as an editable `.pth` path entry -> `~/AgnosticFL/flhub_core/src` (so a `git pull` on this VM changes the hospital's training code; relevant for the FAB rollout and for S3-frozen).
- Next: S1 research (payload limits, FAB rollout, native FedAvg, SuperLink restart) via scouts.

### 2026-10-13T13:56Z — S1 — hospital flhub_core pinned; research done
- Hospital venv: flhub_core 0.1.0 now a fixed copy of `stage1-final` (`deploy/flower-vm/install.sh`, like nvflare b72c4e8); code_version 0.1.0+9c6b064c1e96 and selfcheck sha256:7c80a5a3… unchanged; ledger row.
- Payload knobs (flwr 1.39.0 source): gRPC max message 2 GiB both ways, not configurable (`supercore/grpc.py:30`); arrays chunked in 5 MiB objects (`FLWR_PRIVATE_MAX_ARRAY_CHUNK_SIZE`, env, both sides); 2 concurrent object pushes/pulls (`FLWR_PRIVATE_MAX_CONCURRENT_OBJ_PUSHES/PULLS`, env); with `--database` objects (weights) live in the SuperLink SQLite `objects` table, ref-counted delete, no VACUUM; SuperLink HTTP API (ServerApp + `flwr run`) = FastAPI/uvicorn, no body limit; FAB ≤ 10 MiB (`common/constant.py:64`); TTL default 12 h.
- FAB: includes `**/*.py|toml|md|yaml|yml|json|jsonl`, honours .gitignore, skips symlinks, `lib/`, `build/`, `__pycache__`, `test_*.py`; unpacked by each fresh `flwr-clientapp` into `$FLWR_HOME/apps/<pub>.<name>.<ver>.<hash8>`, then `sys.path.insert(0, app_dir)` -> a `flhub_core/` inside the FAB wins over the venv copy; connected SuperNodes fetch a new FAB per run (no restart).
- Restart: a SuperLink restart kills superexec and the relay ServerApp (parent-pid monitors); nothing relaunches it except our adapter. Node rows persist in the DB, SuperNodes retry forever. Offline = no heartbeat for 60 s (beats every ~20 s); then pulls return NODE_UNAVAILABLE.
- Native FedAvg (`flwr.serverapp.strategy.FedAvg`): p_k = n_k/N pre-normalised, accumulated in the reply dtype (float32), reply order; adds `server-round` (not `round`) to the config; `random.sample` unseeded.

### 2026-10-13T14:25Z — S1 — flhub/0.2 Flower adapter + FAB rollout, island conformance v2 11/11
- Island on the flower VM (`deploy/flower-vm/island.sh up|down|status`): hub core :8800 + SuperLink 127.0.0.1:9092/9093 + adapter :8813 from `~/venvs/island`, SuperNodes flw-h5/flw-h6 from the hospital venv on ports 19094/19095 (production nodes untouched).
- Adapter (`adapters/flower/service.py`): stages `clients/flower` + `flhub_core` into the FAB (ADR-016), `fab-id` check, `/relay/beat`, lost relay -> its rounds fail at once + relaunch (stale 20 s, retry 10 s), `max_payload_mb` 2048, sites flw-h5 flw-h6 + E5 extras flw-h11 flw-h12 (status.sh health = base sites).
- Relay (`clients/flower/flhub_flower/server_app.py`): per-site results/errors as replies arrive (flhub/0.2 `post_site_result`/`post_site_error`), polls `/relay/job` every 5 s and drops sites no longer wanted (late replies pulled and discarded, logged), sends to available sites at once while unmapped sites get <= min(60 s, deadline/4) to connect, restarted SuperNode = lost node within ~5 s of its site-info answer.
- Evidence: FAB precedence (hospital venv 0.1.0, FAB marker 0.1.99 reported); SuperLink SIGKILL mid-round 3 of 8: round failed after 25 s, retried, run finished 8/8; `pytest tests/flower` 10 passed; conformance v2 vs island `--sites flw-h5 flw-h6 --restart-cmd stop+start`: 11 passed, max_abs_diff 0.0 every round, offline-site round 8.0 s, back online 33 s after a full server-side restart.

### 2026-10-13T14:45Z — S1 — E6 tools (inject.sh, straggler.sh) tested on the island
- `inject.sh arm <site> kill|kill-node|restart|straggler-on:<pct>|straggler-off --round N --after S` (vantage6 CLI), log-triggered on the SuperNode's `ROUND N` train message; `straggler.sh on|off|release|status` via user scope `flhub-straggler-<site>.scope` + CPUQuota (ADR-017).
- Island, 4 rounds, round_retries 2: kill-node r2+3s -> SuperNode back 11.1 s, round failed 26 s after kill (relay: replaced node), retry ok (39 s round); kill (ClientApp) -> Flower CLIENT_APP_CRASHED "ClientApp stopped responding" after 58.1 s, retry ok (70 s round); restart -> NODE_UNAVAILABLE 1.2 s after SIGTERM, back 11.6 s (34 s round). All final global_ref = clean run sha256:f5c8f670… (retries bit-exact).
- Straggler 10 %: rounds 43-50 s vs 8 s, same weights; SuperNode start at 10 % takes ~20 s (do `on` before the run); set-property 25 % / off / release verified (cpu.max 25000 100000 -> infinity -> normal cgroup).
- Bug fixed before commit: `node_port` returned 1 for non-last sites (set -e abort after stop).

### 2026-10-13T14:52Z — S1/S2 prep — data provisioned, E5 scripts, 0.2 FAB rollout on the island
- Data: all stage-2 variants for flw-h5/flw-h6 + organamnist-iid12 for flw-h11/flw-h12 in `~/flhub-data/<variant>-<site>.csv`, sha256-verified against the manifests (44 s, 258 MB; 14 GB free). Ledger row.
- E5: `deploy/flower-vm/extra.sh start|stop|status` (runtime `~/flhub-runtime/flower-e5`, ports 9096/9097, own cron line); start.sh accepts variant-only sites.
- Rollout rehearsal on the island: adapter restart -> FAB with flhub_core 0.2.0+fe357a1dbec1; island SuperNodes (hospital venv, 0.1.0 pinned) report 0.2.0+fe357a1dbec1, no SuperNode restart. Conformance v2 incl. payload-64 echo (10.5 s) and variant round organamnist-dir0.3 (7.0 s): 12 passed, diff 0.0; variant round organamnist-iid12 on flw-h5/h6/h11/h12: 2880 examples each, diff 0.0 (9.5 s).

### 2026-10-13T15:05Z — S2 prep — E7 native, purge, restart, netem, sysmon, selfcheck v2
- E7 native (`deploy/central/flower/native/`): Flower's built-in `FedAvg` (fraction_train 1, no federated eval) + hooks only for "round" in the config, a Grid limited to one node per site (relay's site-info mapping) and hub-format checkpoints/eval (hub RunDir + Evaluator) via evaluate_fn; strict (all sites must reply). `run.sh <config> <out_dir>` stages the FAB (native app + same flhub_flower ClientApp + flhub_core), `flwr run` as its own run next to the idle relay run (no pause needed), waits for finished:completed, saves `flwr_run.json` (Flower's bytes/compute) + `serverapp.log`.
- Island dry runs (same config through hub and native): C1 cnn-small iid 3 rounds and C2 payload-45 5 rounds -> global_ref identical in every round, site updates hash-identical, test acc identical. Reason: flw-h5/flw-h6 both have n=5760 -> p_k = 0.5, each float32 term exact, both paths round the same exact sum once. Native round 8.0 s (C1) / 7.4 s (C2) vs hub 6.6-7.8 / 8.1-9.5 s on the island (local, not representative). Flower's `Grid.send_and_receive` polls every 3 s (native round quantised to 3 s).
- `deploy/central/flower/purge.sh`: stop, WAL checkpoint + VACUUM, start (island: 261 MiB -> 0.6 MiB, HEALTHY in 32 s). After payload rounds the SuperLink DB keeps freed pages (objects 0, freelist 99 %).
- `deploy/central/flower/restart.sh adapter|superlink [--kill]` (rollout / E6e) waits for a new relay run: adapter 40 s, SuperLink SIGKILL 61 s. Fixed false HEALTHY after a SuperLink kill (adapter now reports sites unknown without a live relay; /relay/status has relay_alive).
- `deploy/flower-vm/netem.sh` (prio + netem leaf, u32 dst 145.38.205.58:9092, revert timer first; profiles wan1g/wan100/wan20/test) written, RUNLOCK requested (#131) for the 1-min test. `deploy/flower-vm/sysmon.sh` started (per-site supernode/superexec/clientapp components).
- Selfcheck v2 in the hospital runtime (hospital venv python + island FAB's flhub_core 0.2): digest sha256:ac074a0b79a2d3c6782765480164c0465ac598359dfe91695e893e208f4e5034 = central's.
- FAB version 0.2.0 (`clients/flower/pyproject.toml`) so hospitals' app dirs show the rollout.

### 2026-10-13T15:16Z — S2 — deployed on central; RQ2 rollout; netem test; E6 plan; E7 precision finding
- netem test under RUNLOCK #132: `netem.sh apply test --minutes 1` -> RTT to :9092 0.77 -> 20.89 ms, bus :7700 unchanged (0.87 ms), SuperNodes stayed connected; cleared (fq_codel default, 0 timers), released #139.
- Deploy-request #144 (5ca0303) -> central #150 PASS: `restart.sh adapter` 15:10:29.2Z SIGTERM -> healthy 15:10:58.9Z (29.6 s), relay run 16123386983733117612, fab 1bea3b8424b4, flhub_core 0.2.0+fe357a1dbec1. SuperNodes unpacked `agnosticfl.flhub-flower.0.2.0.3733e3db` at 15:10:50Z (+21.5 s), pids unchanged. Selfcheck v2 in that runtime (hospital venv + FAB): sha256:ac074a0b… on flw-h5 and flw-h6 (#151). RQ2 Flower: 0 hospital steps, 0 restarts, 0 s hospital downtime. Ledger row.
- E6 plan reply #145: E6a `kill-node --round 15 --after 4` (measured ClientApp: start +0.3 s, train +3.2..+4.8 s, exit +5.0 s); E6b `straggler.sh prepare flw-h6` before the run + arm `straggler-on:10` at round 14; E6e `restart_server.sh` (5ca0303).
- E7 precision (island, extra, not a final run): partition qty (flw-h5 2194, flw-h6 4389, p_k not exact in float32), 10 rounds, `native/compare.py`: max|diff| 3.0e-8 (r1) -> 1e-6..7e-6 (r2-4) -> 2e-2 (r6+), acc equal to r4 then within 0.6 pp; round-1 site updates hash-equal. With equal sizes (E7 iid configs) native == hub bit-exactly. Native rounds on the island slower (median 9.5 s vs hub 7.8 s): `send_and_receive` polls every 3 s.

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

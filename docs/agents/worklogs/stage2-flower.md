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

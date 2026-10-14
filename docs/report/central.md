# Central agent — report section

## 1. Scope and ownership

Lead and integrator on the central VM (145.38.205.58). Owned: `flhub_core/` (shared training library), `hub/core`, `hub/orchestrator`, `hub/sim`, `adapters/common` and `adapters/mock`, the protocol (`docs/agents/PROTOCOL.md`), `tests/conformance` and the hub unit tests, `scripts/`, `configs/`, `deploy/central/*.sh` (not the per-framework subdirectories), `deploy/versions.env`, the central deployment of all framework servers and adapters, the final W&B runs, `README.md` and `REPORT.md`.

## 2. What I built

| Component | Files | Key design choices |
|---|---|---|
| Shared library | `flhub_core/src/flhub_core/{data,model,train,weights,selfcheck}.py` | PneumoniaMNIST, 6 IID partitions with pinned sha256 (ADR-001); 51,618-parameter CNN without normalization layers; `train()` deterministic: 1 thread, deterministic algorithms, fresh SGD per call, data order seeded by (seed, round, data fingerprint), so hospitals need no configuration (ADR-003); weights = safetensors with the tensor order in its metadata; `code_version()` = sha256 of the package sources (no git needed in images). |
| Protocol | `docs/agents/PROTOCOL.md`, `adapters/common/protocol.py` | `flhub/0.1` frozen at 12:17 UTC: client config = train_config + round; `train_config_hash` = sha256 of canonical JSON; envelope rejection rules incl. finite weights; relay pattern documented. |
| Adapter toolkit | `adapters/common/{server,base,relay,tasks,envelope,hubclient,rpcclient}.py` | stdlib only (no Flask/FastAPI, so no dependency clash with NVFlare 2.9 / Flower 1.39 / vantage6 4.15 venvs). `BaseAdapter`: a framework adapter implements `site_status()` + `run_round()`; the base does protocol checks, error codes, task registry, deadline watchdog, cancel, blobs and envelopes. `RelayAdapter`/`RelayClient`: long-poll job queue for frameworks whose server workflow lives inside the framework server (used by NVFlare and Flower). |
| Hub core | `hub/core/server.py` | content-addressed blob store (PUT verifies sha256) + model spec, systemd unit `flhub-hubcore`. |
| Orchestrator | `hub/orchestrator/{config,executors,fedavg,run,tracking}.py` | config-driven; parallel dispatch to all adapters; strict validation (all sites must participate, else the round fails; `round_retries` re-runs the same deterministic round); FedAvg accumulated in float64 in site_id order, so the result is independent of reply order; global eval on the 624-image test split; checkpoints per round outside the repo; W&B offline by default. `--only` selects adapters from one config, so all final runs share `configs/final.yaml`. |
| Reference runs | `hub/sim/__main__.py` | the same round loop with an in-process executor (FedAvg reference) or one pooled pseudo-site (centralized baseline). |
| Admission test | `tests/conformance/` | discover schema, all sites online, error codes, round 1 equals direct `flhub_core.train` (≤ 1e-6), busy (-32015) then round 2, cancel then round 4; `--adapter mock` self-test; `--report` JSON. |
| Scripts | `scripts/{equivalence_check,wandb_cleanup}.py`, `scripts/run_final.sh` | per-round max abs weight difference + final metric difference, written into the W&B summary; cleanup is a dry run unless `--apply`. |
| Central deploy | `deploy/central/{install,start,stop,status}.sh` | hub venv, dataset, Docker registry `registry:2` on :5050, hub core systemd unit; `status.sh` aggregates bus, hub core, registry and every `deploy/central/<fw>/status.sh` (exit 0 = all six sites online). |

ADRs written: ADR-001 (dataset), ADR-002 (versions), ADR-003 (determinism without hospital configuration).

## 3. Framework specifics (central's view)

- Versions pinned in `deploy/versions.env`: Python 3.11.17, torch 2.14.1+cpu, numpy 2.4.4 (numpy 2.5 needs Python ≥ 3.12), safetensors 0.8.0, NVFlare 2.9.0, vantage6 4.15.2 (5.x excluded), flwr 1.39.0, wandb 0.30.0. NVFlare pulls protobuf 7.x and flwr protobuf 6.x, so one venv per framework (`~/venvs/{hub,nvflare,v6,flower}`) was necessary; uv hardlinks from its cache, so each extra venv costs ~80 MB.
- Flower 1.39 no longer has a ServerAppIo port: the SuperLink exposes one HTTP API (`--host/--port`, here 127.0.0.1:9093) for ServerApps and `flwr run`; 9091 is unused (found by the flower agent; topology updated, ADR-007).
- Cross-VM bit-exactness: all four VMs are AMD EPYC-Rome (AVX2); `python -m flhub_core.selfcheck` gave the same sha256 (`7c80a5a3…`) on every VM and inside the vantage6 image, so no CPU-feature pinning was needed (fallback documented in ADR-003).

## 4. Central deployment footprint

| Service | Port | Started by |
|---|---|---|
| agent bus | 0.0.0.0:7700 | systemd `agentbus` (bootstrap) |
| hub core | 127.0.0.1:8800 | systemd `flhub-hubcore` (`deploy/central/start.sh`) |
| Docker registry | 0.0.0.0:5050 | `registry:2`, restart always |
| NVFlare server / admin | 0.0.0.0:8002 / 8003 | `deploy/central/nvflare/start.sh` (startup kit) |
| NVFlare adapter + relay job | 127.0.0.1:8811 | same |
| vantage6 server | 0.0.0.0:7601 | `deploy/central/vantage6/start.sh` (Docker) |
| vantage6 adapter | 127.0.0.1:8812 | same |
| Flower SuperLink Fleet / HTTP API | 0.0.0.0:9092 / 127.0.0.1:9093 | `deploy/central/flower/start.sh` |
| Flower adapter + relay run | 127.0.0.1:8813 | same |

Credentials sent with `tools/bus put --secret`: NVFlare kits nvf-h1/nvf-h2 (files #1, #2), vantage6 node API keys (file #3). Flower runs without TLS (firewall-restricted, ADR-007).

## 5. Timeline (UTC, 2026-10-13)

| Time | Event | SHA / bus |
|---|---|---|
| 11:59 | bus up, P0 start | #2 |
| 12:10 | flhub_core pushed; selfcheck requested | c703faf, #9 |
| 12:11–12:15 | selfcheck bit-exact on flower, nvflare, vantage6 VM + image | #11, #12, #14 |
| 12:17 | **M0-core**: adapters/common, hub core, PROTOCOL frozen | bbe6f2a, #15 |
| 12:19 | conformance suite | f039570, #18 |
| 12:25 | orchestrator, sim, equivalence check | 5b05dbd, #19 |
| 12:28 | **M1-hub** (mock vs sim diff 0.0; hub core systemd, registry, venvs) | 0ac748d, #20 |
| 12:34 | NVFlare deployed on central, kits sent | #29 |
| 12:38 | **M2-nvflare** | #36 |
| 12:42 | vantage6 deployed on central, node keys sent | #39 |
| 12:46 | Flower deployed on central; **M2-vantage6** | #49, #50 |
| 12:50 | **M2-flower** | #54 |
| 12:51 | **M2-mixed** (6 sites, diff 0.0) | #55 |
| 12:51 | P3 freeze of configs/final.yaml; confirmations 12:52 | b656577, #56, #59–#61 |
| 12:53–13:08 | six final runs online; equivalence PASS | run_final.sh |
| 13:09 | **M3-final**; final-call | #66, #67 |

## 6. Problems and fixes

| Problem | Component | Symptom | Root cause | Fix | Commit | Time lost |
|---|---|---|---|---|---|---|
| Package shadowed | flhub_core | `ImportError: cannot import name 'code_version' from 'flhub_core' (unknown location)` when run from the repo root | setuptools' editable install uses a meta-path finder that runs after PathFinder; the outer `flhub_core/` dir became a namespace package | src layout (`flhub_core/src/flhub_core`), editable install becomes a plain `.pth` path | 635a31b | 5 min |
| Shell killed itself | ops | exit 144 from one-line commands | `pkill -f <pattern>` matched the calling shell, whose command line contained the pattern | kill by PID / pid files | — | 3 min |
| Slow start of FedAvg | training config | test acc stuck at the majority class (0.625) for ~12 rounds | small lr, no normalization | tried lr 0.1/0.2 (87.2 % / 86.1 % vs 86.5 %): no clear gain, plan default kept | — | 5 min |
| Port map outdated | topology | Flower 1.39 has no ServerAppIo port | Flower API change | 9091 unused, 9093 = SuperLink HTTP API (reported by flower) | 4588076 | 0 |
| Status exit codes | deploy | vantage6/flower status.sh returned 0 with sites offline | different conventions | requested exit 1 unless all sites online | 9c88bca, 8b19ca5 | 5 min |

No dead ends on the hub side: every adapter passed the conformance suite against the central deployment on the first attempt.

## 7. Tests and evidence

- Unit tests: `pytest` → all pass (flhub_core, adapters/common, hub core, relay, FedAvg, config, equivalence; framework adapter tests skip where the framework is not installed).
- Conformance vs the central deployment: NVFlare 7/7 (13.7 s), vantage6 7/7 (26.4 s), Flower 7/7 (30.2 s); max_abs_diff 0.0 in rounds 1, 2, 4 for all six sites (`docs/report/evidence/conformance-*-central.json`).
- P2 3-round runs (offline) vs `hub.sim`: nvflare, vantage6, flower, mixed: max abs weight diff 0.0 every round (`evidence/equivalence-mixed-p2.json`).
- Final runs (W&B, group `final-2026-10-13`): mixed-6sites vs sim-fedavg-reference: max abs weight diff 0.000e+00 in rounds 0–30, final accuracy diff 0.0 pp, identical final model bytes (`evidence/equivalence-final.json`); W&B cleanup: 6 runs, keep 6, delete 0 (`evidence/wandb-cleanup-apply.txt`).

## 8. Open issues and recommendations

- The hub sees plaintext model updates; secure aggregation (MPC or threshold HE) would need a protocol change (`flhub/0.2`) because FedAvg would move out of the orchestrator.
- Single operator and single hub: the central VM hosts every framework server. A real network would separate the framework servers per consortium.
- Bit-exactness relies on identical CPUs and library versions; on mixed hardware use the CPU-capability pinning from ADR-003 or the metric tolerance.
- The protocol has no evaluation method yet (`capabilities.evaluate=false`); federated evaluation at the sites would be a natural `flhub/0.2` addition.

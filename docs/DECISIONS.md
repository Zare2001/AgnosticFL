# Architecture decision records

One ADR per decision that deviates from or fills a gap in docs/agents/PROJECT_PLAN.md.
Format: `## ADR-<n>: <title>` with **Context**, **Decision**, **Consequences** (≤ 12 lines each), plus date and author role.

<!-- ADR-001 (dataset choice) is written by the central agent in phase 0. -->

## ADR-001: Dataset = PneumoniaMNIST (MedMNIST v2), one CSV per site
Date 2026-10-13, central.
**Context**: PROJECT_PLAN.md §5 needs a public, login-free, health-related dataset that trains to a meaningful score on CPU in minutes and fits one CSV/Parquet file per site.
**Decision**: PneumoniaMNIST (Yang et al., MedMNIST v2, CC BY 4.0; source: Kermany et al. chest X-rays). File `pneumoniamnist.npz` from Zenodo record 10519652 (4.17 MB, md5 `28209eda62fecd6e6a2d98b1501bb15f`, sha256 `e1792d3f03751cb101e99f19a63b3c1941436c988665f47853417b05be250cd8`). 28x28 grayscale, binary (normal / pneumonia).
Official train split (4,708) → 6 IID partitions with `RandomState(2026).permutation` (frozen numpy stream): 785/785/785/785/784/784 for nvf-h1, nvf-h2, v6-h3, v6-h4, flw-h5, flw-h6. One CSV per site (`label,x0..x783`, integer pixels), sha256 of every site file pinned in `flhub_core.data.PARTITION_SHA256`. Test split (624, 62.5 % pneumonia) stays on the central VM for global evaluation; the val split is unused.
Model: CNN conv(1→8)→pool→conv(8→16)→pool→fc(784→64)→fc(64→2), ReLU, no normalization layers; 51,618 parameters, 206 KB float32.
**Consequences**: tiny payloads and ~1 s per local round per site on one CPU thread; binary task gives accuracy + AUC. MedMNIST's test split is shifted against train (published ResNet-18 test acc ≈ 85 %), so expect ~85 % rather than the ~95 % validation accuracy. The CDC diabetes table was rejected because its accuracy barely beats the 86 % majority baseline.

## ADR-002: Version pins (deploy/versions.env)
Date 2026-10-13, central.
**Context**: the plan asks for latest stable versions, Python 3.11 and vantage6 4.15.x.
**Decision**: Python 3.11.17 (uv), torch 2.14.1+cpu, numpy 2.4.4, safetensors 0.8.0, NVFlare 2.9.0, vantage6 4.15.2, flwr 1.39.0, wandb 0.30.0, registry:2. numpy 2.5 requires Python ≥ 3.12, so 2.4.4 is the newest usable; vantage6 5.x (latest 5.0.3, Python ≥ 3.13, Kubernetes) is excluded by the plan.
**Consequences**: every VM and the vantage6 image install torch from the CPU index with the exact same version, a precondition for bit-identical training.

## ADR-003: Determinism without hospital-side configuration
Date 2026-10-13, central.
**Context**: §5 seeds the data order with (seed, round, partition_index). A real hospital has no "partition index", and passing one would be hub-specific client config.
**Decision**: the data order is seeded with sha256(`seed`, `round`, data fingerprint), where the fingerprint is the sha256 of the site's canonical uint8 pixels + int64 labels. Same data => same order, with zero configuration; the reference simulation reproduces it by loading the same files. Training uses 1 CPU thread, `use_deterministic_algorithms(True)`, a fresh SGD (foreach=False) per call, no dropout. `python -m flhub_core.selfcheck` prints the sha256 of one local round; all VMs and the vantage6 image must print the same value.
**Consequences**: bit-exact cross-framework FedAvg is possible if all CPUs take the same kernel paths (central VM: AMD EPYC-Rome, AVX2). If selfcheck differs across VMs, the fallback is pinning `ATEN_CPU_CAPABILITY`/`MKL_CBWR`/`ONEDNN_MAX_CPU_ISA` (new ADR).

## ADR-004: vantage6 algorithm image on python:3.11 instead of algorithm-base:4.15
Date 2026-10-13, vantage6.
**Context**: the official `ghcr.io/vantage6/infrastructure/algorithm-base:4.15` is `python:3.10-slim` + `vantage6-common` + `vantage6-algorithm-tools` (numpy 2.2.6, pandas 2.3.3). The plan pins Python 3.11 and the same torch/numpy everywhere (ADR-002).
**Decision**: `clients/vantage6/Dockerfile` builds FROM `python:3.11.17-slim-bookworm` and installs exactly what the base image adds (`vantage6-algorithm-tools==4.15.2`, pandas 2.3.3) plus torch 2.14.1+cpu, numpy 2.4.4, safetensors 0.8.0, flhub_core and the algorithm package `v6_flhub` (`PKG_NAME=v6_flhub`, CMD = the standard `wrap_algorithm()`). Image: `<registry>/flhub/v6-train:<git sha>`.
**Consequences**: still a standard vantage6 algorithm (nodes need no change); `python -m flhub_core.selfcheck` inside the image prints the same result hash as every VM (sha256:7c80a5a3…).

## ADR-005: vantage6 4.15.2 CLI workarounds on Python 3.11
Date 2026-10-13, vantage6.
**Context**: vantage6 4.15.2 declares Python ≥ 3.10, but its CLI f-strings `(str, Enum)` members; Python 3.11 formats them as `Class.MEMBER`. Effects: `v6 server import` passes its DB location as env var `ServerGlobals.DB_URI_ENV_VAR` instead of `VANTAGE6_DB_URI`, so the import writes into a throw-away container DB; the server container is named `vantage6-<name>-user-ServerType.V6SERVER`, so `v6 server stop -n` cannot find it. Also, server/node containers (root) write the shared log dir, so later CLI calls fail with "Can't write to log dir", and a fresh server creates superuser `root`/`root` unless a user `root` exists.
**Decision**: install.sh runs the server image's own `vserver-local import` with `VANTAGE6_DB_URI` set; the server is started with `v6 server start -c <config> --user` (no CLI file logging) and stopped by Docker label; nodes are stopped with `docker stop vantage6-<site>-user`; the entities file imports `root` with a generated password. Server and node configs are written from templates (`v6 server new`/`v6 node new` are interactive only).
**Consequences**: no patched vantage6 code; standard images and config formats. Revisit if the CLI is upgraded.

## ADR-006: NVFlare relay = one long-running job, kept alive by the adapter
Date 2026-10-13, nvflare.
**Context**: PROJECT_PLAN.md §3 puts a ModelController relay inside the NVFlare server, but does not say how its job lives, how hub sites map to NVFlare clients over time, or how "online" is decided. Observed in NVFlare 2.9.0: (a) the server keeps a stopped client "connected" (`get_system_info`, `get_client_disconnect_time`) for minutes; (b) a restarted client does not rejoin a job that is already running; (c) `ScriptRunner` with an absolute script path makes clients run that local path instead of the shipped `custom/` copy.
**Decision**: one job `flhub-relay` (FedJob, min_clients = 2, deploy `@ALL`): server workflow `adapters.nvflare.relay_controller.HubRelayController` from the installed package, clients `ScriptRunner("client.py", framework=NUMPY)` in-process (relative path, so the job ships `custom/client.py`). The adapter (`adapters/nvflare/service.py`) submits it through the FLARE API with the operator's admin kit, adopts a running one, and restarts it gracefully (relay shutdown, then resubmit) when a connected site has been outside the job for 15 s and no round is in flight. A site is online only if the relay reports it as a live job client AND the server's `get_client_job_status()` probe shows the client running that job (a dead client answers `no_reply`). A round on an offline site fails at once instead of waiting for the deadline.
**Consequences**: hospital restarts and server restarts heal without operator action (verified: offline within 5 s; both online again ~50 s after a client restart; ~20 s after a server restart). A job restart briefly takes both NVFlare sites offline. Training code reaches hospitals only as job custom code (BYOC, allowed for the project_admin submitter; BYOC jobs bypass the 2.9 client class allow-list).

## ADR-007: Flower 1.39 server side: one HTTP API port, persistent SuperLink, supervised SuperNodes
Date 2026-10-13, flower.
**Context**: Flower 1.39 dropped the ServerAppIo gRPC API (plan port 9091): `flwr run` and the ServerApp both use one SuperLink HTTP API (`--host/--port`), and `--clientappio-api-address` became a per-SuperNode `--port`. The SuperLink installs FAB dependencies at runtime by default. A SuperNode exits (self-SIGINT on `UNAUTHENTICATED`) when a restarted in-memory SuperLink no longer knows its node id, so a central redeploy would silently take the hospitals offline. SuperNode ids are random per start; the ServerApp cannot see `--node-config`.
**Decision**: SuperLink `--insecure --fleet-api-address 0.0.0.0:9092 --host 127.0.0.1 --port 9093 --database ~/flhub-runtime/flower/superlink.db --disable-runtime-dependency-installation` (9093 = local HTTP API, 9091 unused; deps preinstalled). Hospitals run the standard `flower-supernode` under `deploy/flower-vm/supernode.sh`, which restarts it if it exits (like systemd `Restart=always`); runtime ports 9094/9095 on the flower VM only, one `FLWR_HOME` per node. The relay ServerApp maps node id -> site with a standard QUERY message answered by the ClientApp's `@app.query()` (newest node wins per site) and reports sites to the adapter; the adapter keeps one relay run alive via `flwr run`.
**Consequences**: SuperNodes keep their ids across SuperLink restarts and recover by themselves if its state is lost; no hub code on hospitals beyond `flhub_core` + the node config (`site-id`, `data-path`). Flower without TLS/SuperNode auth stays a documented test-network limitation (stretch goal).

# AgnosticFL — Project plan

## 1. Mission

Build a working federated learning network in which **each hospital keeps using its own framework** (NVFlare, vantage6 or Flower) **without hub-specific code changes**, while one **central hub** runs plain FedAvg across all of them. The central server contains, per framework, an **adapter** that makes the hub look like a normal part of that framework. The hub itself is framework-free: it only sees model weights, sample counts and metrics.

Proof of success: one training run in which 6 simulated hospitals (2 per framework) train one shared model together, logged to Weights & Biases, numerically equivalent to a framework-free FedAvg reference run.

### Working assumptions (given by the project owner)

- Data at every hospital is good, code is correct, nothing is malicious, nothing breaks by itself. **No anomaly detection, secure aggregation or DP is required.** Plain, weighted FedAvg.
- Minimal changes on hospital nodes: a hospital installs only its framework's standard client and runs the framework's standard client pattern. Only connection settings point to the central VM.
- Security is "reasonable for a test network": TLS where the framework provides it by default (NVFlare), firewalls restricted to the 4 VM IPs, secrets never in git. Hardening is out of scope and is listed as future work in the report.

## 2. Topology

| VM | IP | Role | Runs |
|---|---|---|---|
| central | 145.38.205.58 (`flhub-central`) | network operator | agent bus, hub core, orchestrator, 3 adapters, NVFlare server, vantage6 server, Docker registry, Flower SuperLink |
| nvflare | 145.38.189.142 | 2 hospitals | NVFlare clients `nvf-h1`, `nvf-h2` |
| vantage6 | 145.38.186.63 | 2 hospitals | vantage6 nodes `v6-h3`, `v6-h4` (Docker) |
| flower | 145.38.187.20 | 2 hospitals | Flower SuperNodes `flw-h5`, `flw-h6` |

All framework **servers** run on the central VM, because in a real network the operator hosts them and hospitals only run clients. The framework VMs only run hospital clients (in phase 1 they also run a temporary local copy of everything, see §6).

### Port map (central VM) — also in `deploy/topology.env`

| Port | Service | Reachable from |
|---|---|---|
| 7700 | agent bus (build-time coordination) | all 4 VMs |
| 8800 | hub core API + blob store | localhost |
| 8811 / 8812 / 8813 | NVFlare / vantage6 / Flower adapter (JSON-RPC) | localhost |
| 8002 | NVFlare server (client connections) | nvflare VM |
| 8003 | NVFlare admin / FLARE API | localhost |
| 7601 | vantage6 server API | vantage6 VM |
| 5050 | Docker registry (vantage6 algorithm image) | vantage6 VM |
| 9091 | Flower SuperLink ServerAppIo | localhost |
| 9092 | Flower SuperLink Fleet API | flower VM |
| 9093 | Flower SuperLink Control API (`flwr run`) | localhost |

The project owner opens the public ports in the cloud firewall. Agents must verify reachability in phase 0 (`nc -zv 145.38.205.58 <port>`) and report missing ports to the owner via `tools/bus send --to human --type blocker` **and** in their status.

### Sites

| site_id | framework | VM | data partition index |
|---|---|---|---|
| nvf-h1 | NVFlare | nvflare | 0 |
| nvf-h2 | NVFlare | nvflare | 1 |
| v6-h3 | vantage6 | vantage6 | 2 |
| v6-h4 | vantage6 | vantage6 | 3 |
| flw-h5 | Flower | flower | 4 |
| flw-h6 | Flower | flower | 5 |

## 3. Architecture

```
                 ┌──────────────────────────────────────────────────────────┐
 central VM      │ ORCHESTRATOR  (hub/orchestrator)  rounds · FedAvg · eval  │
                 │ HUB CORE      (hub/core)  model registry · blob store      │
                 └───────▲───────────────────▲───────────────────▲──────────┘
                  flhub/0.1 JSON-RPC: discover · dispatch · tasks/get · collect · cancel
                 ┌───────┴───────┐   ┌───────┴───────┐   ┌───────┴───────┐
                 │ NVFlare       │   │ vantage6      │   │ Flower        │
                 │ adapter :8811 │   │ adapter :8812 │   │ adapter :8813 │
                 │ + relay job   │   │ (v6 client)   │   │ + relay       │
                 │ (ModelCtrl)   │   │               │   │ ServerApp     │
                 ├───────────────┤   ├───────────────┤   ├───────────────┤
                 │ NVFlare server│   │ vantage6      │   │ Flower        │
                 │ :8002         │   │ server :7601  │   │ SuperLink     │
                 └───────▲───────┘   └───────▲───────┘   └───────▲───────┘
 framework VMs   NVFlare clients      vantage6 nodes        Flower SuperNodes
 (hospitals)     nvf-h1, nvf-h2       v6-h3, v6-h4          flw-h5, flw-h6
                 standard Client API   standard algorithm    standard ClientApp
```

### What "no changes on hospital nodes" means per framework

| Framework | Hospital installs | Training code reaches the hospital via | Only hospital-side config |
|---|---|---|---|
| NVFlare | NVFlare + torch + `flhub_core` in a venv; startup kit from the operator | the job's custom code (standard NVFlare job deployment) | startup kit (server = `flhub-central:8002`) |
| vantage6 | vantage6 node (Docker) | the algorithm image pulled from the operator's registry (standard) | node config: server URL, API key, data file |
| Flower | `flwr` + torch + `flhub_core` in a venv | the Flower App Bundle (FAB) shipped by `flwr run` (standard) | `flower-supernode --superlink 145.38.205.58:9092 --node-config ...` |

`flhub_core` is an ordinary shared Python library (model, data loading, local training loop). It is the "same training code" for all frameworks, which is what makes FedAvg across frameworks valid. The client wrappers are the frameworks' own documented patterns, ~50 lines each.

### How each adapter works (server side only)

- **NVFlare adapter**: a long-running NVFlare job whose server workflow is a custom `ModelController` subclass (`HubRelayController`). Its `run()` loop polls the adapter service (localhost) for the next dispatch, calls `send_model_and_wait()` to the NVFlare clients, converts the returned `FLModel`s to envelopes and posts them back. Clients use the standard NVFlare Client API (`flare.receive()` / `flare.send()`) and must send FULL weights (not DIFF) — or the relay converts.
- **vantage6 adapter**: the adapter is a vantage6 *user* of an organisation `hub-operator`. On `dispatch` it creates a vantage6 task (method `train`, global weights inline as base64 safetensors) for the two hospital organisations, polls it, and on `collect` decodes the results into envelopes. The algorithm image is built from `clients/vantage6/` + `flhub_core` and pushed to the registry on :5050.
- **Flower adapter**: a long-running Flower run (`flwr run` against the central SuperLink) whose `ServerApp` is a relay: it polls the adapter service, sends `Message`s (train) with an `ArrayRecord` to the connected SuperNodes via the `Grid`, converts replies to envelopes and posts them back. Hospitals run a standard `ClientApp` (`@app.train()`).

Verify every framework API above against the installed version before coding (`scout`).

## 4. Repository layout and ownership

```
flhub_core/          shared library: model, data, train/eval, weights io      owner: central
hub/core/            hub core API, model registry, blob store                 owner: central
hub/orchestrator/    round loop, FedAvg, global eval, W&B logging             owner: central
hub/sim/             framework-free FedAvg reference simulation               owner: central
adapters/common/     JSON-RPC server base, envelope helpers                   owner: central
adapters/nvflare/    adapter service + NVFlare relay job                      owner: nvflare
adapters/vantage6/   adapter service (vantage6 client)                        owner: vantage6
adapters/flower/     adapter service + Flower relay ServerApp                 owner: flower
clients/nvflare/     standard NVFlare Client API training script              owner: nvflare
clients/vantage6/    vantage6 algorithm (Dockerfile + package)                owner: vantage6
clients/flower/      standard Flower ClientApp (+ pyproject for the FAB)      owner: flower
deploy/central/      install/start/stop/status scripts for the central VM     owner: central (subdirs nvflare/, vantage6/, flower/ owned by those agents)
deploy/<fw>-vm/      scripts for each framework VM                            owner: that framework agent
configs/             run configs (yaml)                                       owner: central
tests/               unit tests, conformance tests                            shared (each owns its files)
scripts/             equivalence check, W&B cleanup, report helpers           owner: central
docs/                plan, protocol, decisions, worklogs, report sections     shared (see COLLABORATION.md)
tools/               agent bus, runner                                        owner: central
```

Touching a directory you do not own: send a `request` to the owner first. Small fixes are allowed after an `info` message; never rewrite another agent's design.

## 5. Technical decisions (fixed unless an ADR in docs/DECISIONS.md changes them)

- **Python 3.11** everywhere (venvs via `uv`), one venv per component on the central VM (`~/venvs/hub`, `~/venvs/nvflare`, `~/venvs/v6`, `~/venvs/flower`) to avoid dependency conflicts.
- **PyTorch, CPU build**, same exact version on all VMs and in the vantage6 image. Determinism: `torch.manual_seed`, `torch.use_deterministic_algorithms(True)`, `torch.set_num_threads(1)` in the training core; data order from a generator seeded with `(seed, round, partition_index)`.
- **Framework versions**: latest stable NVFlare 2.x; **vantage6 4.15.x** (Docker-based; do NOT use 5.x, which needs Kubernetes); latest stable Flower. Pin exact versions in `deploy/versions.env` in phase 0 and record them in the report.
- **Dataset**: the central agent chooses in phase 0 and records ADR-001. Requirements: public, downloadable without login, medical or health-related, trains to a meaningful score on CPU in minutes, and can be stored as **one CSV or Parquet file per site** (the same file format for all frameworks; vantage6 nodes read the same file). Shortlist: MedMNIST (e.g. PneumoniaMNIST or BloodMNIST, flattened), CDC Diabetes Health Indicators (UCI, tabular), scikit-learn breast cancer (fallback only, too small). `flhub_core.data` deterministically materializes partition *i* of 6 (IID, fixed seed) to `~/flhub-data/<site_id>.<ext>` and verifies the dataset checksum. The official test split stays on the central VM for global evaluation.
- **Model**: small and payload-friendly (≤ 2 M parameters, ≤ 8 MB as float32), **no BatchNorm** (use none or GroupNorm) so FedAvg has no buffer semantics.
- **Canonical weights**: ordered mapping `name → float32 ndarray` using the PyTorch `state_dict` names of the `flhub_core` model; serialized with `safetensors`; identified by `sha256:<hex>` of the bytes.
- **FedAvg**: `w_new = Σ_k (n_k / Σ_j n_j) · w_k`, accumulated in float64, cast to float32. `n_k` = number of training examples at site k. Clients train from the global weights with a fresh optimizer every round.
- **Default training config** (may be tuned in phase 1, then frozen in `configs/final.yaml`): 30 rounds, 1 local epoch, batch 64, SGD lr 0.05, momentum 0, seed 2026.
- **Transport inside the central VM**: adapters and hub core on localhost, weights passed by blob reference (PROTOCOL.md §4). Across VMs: each framework's own native channel only.
- **Framework security defaults**: NVFlare with its provisioned mTLS. vantage6 and Flower may run without TLS in this test network, restricted by firewall to the VM IPs; this is documented as a limitation. Optional stretch: Flower TLS + SuperNode authentication.

## 6. Phases, milestones and sync points

Milestones are announced with `tools/bus send --to all --type milestone --subject "<ID> <one line>"` and reflected in `tools/bus status-set`.

### P0 — Bootstrap (all agents, target < 3 h)
- Verify Claude config: make a throwaway commit in a scratch repo and confirm no attribution trailer appears; confirm the hooks block a bad commit.
- Install base toolchain (`uv`, Python 3.11, docker where needed). Check connectivity to every central port you will need; report gaps.
- **central**: repo skeleton, `flhub_core` v0 (dataset ADR-001, model, data partitioning, `train()`/`evaluate()`, weights io), `PROTOCOL.md` frozen as `flhub/0.1`, `adapters/common` (JSON-RPC base + envelope dataclass), `deploy/versions.env`. Milestone **M0-core**.
- Framework agents: read the plan; prototype against their framework's installed version in parallel (no dependency on M0 for reading docs and installing).

### P1 — Islands (parallel, target < 1 day)
Each framework agent builds a complete vertical slice **on its own VM**: local hub core + orchestrator (from the repo), local framework server, its adapter, and two local hospital clients. Pass criteria:
1. `tests/conformance/test_adapter.py --adapter http://127.0.0.1:<port>` passes: `discover` is valid; one round of `dispatch → tasks/get → collect` returns 2 envelopes whose weights match `flhub_core.train` run directly on the same partitions with the same seed (max abs diff ≤ 1e-6).
2. A 3-round island run through the orchestrator completes (W&B offline).
3. Scripts exist and are idempotent: `deploy/central/<fw>/{install,start,stop,status}.sh` (to run the framework server + adapter on the central VM) and `deploy/<fw>-vm/{install,start,stop,status}.sh` (hospital clients pointing at the central VM).
Milestone **M1-<fw>** per framework.

**central** in P1: hub core, orchestrator, FedAvg, global eval, W&B logging, `hub/sim` reference, conformance test suite, a `mock` adapter for testing, `scripts/equivalence_check.py`, `scripts/wandb_cleanup.py`. Installs Docker registry on :5050 and base framework installs on the central VM. Milestone **M1-hub**.

### P2 — Integration (central-led)
For each framework, in the order the M1 milestones arrive:
1. Framework agent sends a `deploy-request` with the git SHA and the exact commands.
2. Central runs `deploy/central/<fw>/install.sh && start.sh`, provisions credentials (NVFlare startup kits / vantage6 node API keys) and sends them with `tools/bus put --secret --to <fw>`. Replies `deploy-result`.
3. Framework agent switches its hospital clients to the central VM (only config changes), stops its island servers.
4. Central runs a 3-round single-framework run through the central hub. Milestone **M2-<fw>**.
Then the mixed 6-site run (3 rounds). Milestone **M2-mixed**.

### P3 — Final runs (central-led; everyone keeps clients up and watches logs)
1. Freeze `configs/final.yaml`. Every agent confirms its clients run the frozen SHA.
2. Run, online to W&B with tag `final` (COLLABORATION.md §6): `centralized-baseline`, `sim-fedavg-reference`, `nvflare-only`, `vantage6-only`, `flower-only`, `mixed-6sites`.
3. `scripts/equivalence_check.py`: mixed run vs reference run, per round. Pass: max abs weight difference ≤ 1e-5 every round and final test-accuracy difference ≤ 0.1 percentage point. If CPU differences across VMs make bit-level equality impossible, document why with evidence and use the metric tolerance (≤ 0.5 pp) — ADR required.
4. `scripts/wandb_cleanup.py` (dry run, then real): only finished runs tagged `final` remain. Milestone **M3-final**.

### P4 — Documentation and report
- `central` broadcasts `final-call`. Each agent writes `docs/report/<role>.md` (REPORT_TEMPLATE.md §per-agent), pushes, sends `report-ready`.
- `central` exports the bus transcript (`tools/bus export -o docs/report/bus_transcript.md`), compiles `REPORT.md`, updates `README.md` (quick start, how to add a new framework adapter), pushes, broadcasts `all-done`.
- On `all-done`, every agent verifies the DoD items it owns, writes its final worklog entry, then `touch ~/.flhub/DONE`.

## 7. Testing

- Unit tests (`pytest`) for `flhub_core`, FedAvg, envelope validation, blob store.
- Conformance suite (`tests/conformance/`): any adapter must pass it; it is also the admission test for future adapters (BranchKey, others).
- Island and integration runs as in §6.
- Equivalence check as in §6 P3.

## 8. Weights & Biases

- Project `AgnosticFL` (entity from `WANDB_ENTITY` or the key's default). Only the **central orchestrator** logs final runs.
- Per run: config (git SHA, framework versions, dataset checksum, partition sizes, training config), per round: `global/test_loss`, `global/test_acc` (+ AUC if binary), `round/duration_s`, `round/num_sites`, per site `site/<id>/train_loss`, `site/<id>/num_examples`, per framework participation. Summary: final metrics, equivalence result.
- Development: offline. See COLLABORATION.md §6 for the cleanup rule.

## 9. Final report (`REPORT.md`)

Follow `docs/agents/REPORT_TEMPLATE.md`. It must be complete enough that a reader who never saw this build can understand the architecture, reproduce it from scratch, add a new framework adapter, and judge the results.

## 10. Definition of Done (all must be true and verified)

1. All services start from scripts in `deploy/` on the correct VMs; `status.sh` on each VM reports healthy.
2. Hospital clients on the 3 framework VMs run the frameworks' standard client patterns; the only hub-specific items on those VMs are connection settings and credentials (verified by the framework agents and listed in the report).
3. Every adapter passes the conformance suite against the central deployment.
4. `mixed-6sites` completed all rounds with all 6 sites participating in every round.
5. The equivalence check passed (or the ADR-justified metric tolerance passed).
6. W&B project contains exactly the final runs listed in §6 P3, all `finished`, tagged `final`; no failed/crashed/dev runs.
7. `main` is green: `pytest` passes; every commit is authored and committed by Zare2001 with no AI attribution (`git log --format='%an|%cn|%B' | grep -Ei 'co-authored|claude|anthropic'` returns nothing relevant).
8. `REPORT.md`, `README.md`, `docs/DECISIONS.md`, per-agent report sections and the bus transcript are pushed.
9. The central agent broadcast `all-done`, and every agent created `~/.flhub/DONE`.

## 11. Fallbacks (use when blocked > 45 minutes; record an ADR)

- vantage6 node cannot pull from the plain-HTTP registry: add `"insecure-registries": ["145.38.205.58:5050"]` to `/etc/docker/daemon.json` on the vantage6 VM; if still blocked, `docker save | docker load` the image onto the vantage6 VM and configure the node to use the local image.
- NVFlare server name / certificate issues with an IP: use the hostname `flhub-central` (already in `/etc/hosts` on all VMs) as the server name in `project.yml`.
- Flower version API mismatch: pin the Flower version that the relay was written against on both central and flower VMs.
- A public port cannot be opened: report to `human` and continue with all other work; never tunnel around the firewall.
- Large model payloads hit a framework limit: shrink the model; do not change the protocol.

# AgnosticFL — final report

Framework-agnostic federated learning across NVFlare, vantage6 and Flower hospitals through one central FedAvg hub. Built on 2026-10-13 by four autonomous agents (central, nvflare, vantage6, flower), one per VM, coordinating over an agent bus and one GitHub repository.

## 1. Executive summary

**What was built.** A working federated network of 6 simulated hospitals: 2 on **NVFlare 2.9.0**, 2 on **vantage6 4.15.2** and 2 on **Flower 1.39.0**. Each hospital runs only its framework's standard client: the NVFlare Client API, a vantage6 node with an algorithm image, and a Flower SuperNode with a ClientApp. One central hub on the operator's VM trains a single shared model with plain weighted FedAvg. Per framework, a server-side **adapter** wraps the framework server and speaks one small framework-free protocol, **`flhub/0.1`** (JSON-RPC: `discover`, `dispatch`, `tasks/get`, `collect`, `cancel`), to the orchestrator. The hub itself only sees weights, sample counts and metrics.

**Does it work?** Yes. All Definition-of-Done items are verified (§12).
- Every adapter passed the conformance suite against the central deployment: 7/7 tests. The weights returned from the remote hospitals equal local training bit for bit (max abs diff 0.0).
- The final 30-round **mixed-6sites** run used all 6 sites in all 30 rounds. It is **bit-identical** to the framework-free FedAvg reference: the max abs weight difference is 0.0 in all 31 checkpoints, and the final model bytes are identical (`sha256:7fd3e2a4…`).

**Headline results** (PneumoniaMNIST test split, 624 images, 30 rounds or epochs):

| Run | Test accuracy | Test AUC | Wall time |
|---|---|---|---|
| mixed-6sites (NVFlare + vantage6 + Flower) | **0.8654** | **0.9247** | 244 s |
| sim-fedavg-reference (framework-free) | 0.8654 | 0.9247 | 20 s |
| centralized-baseline (pooled data) | 0.8606 | 0.9279 | 20 s |

Federated training across three frameworks matches centralized training on the pooled data, and matches the framework-free simulation exactly.

**Build time.** About 70 minutes from "bus up" (11:59 UTC) to the six final runs on W&B (M3-final, 13:09 UTC). The report followed.

**Main limitations** (§13):
- This is a test network. The hub sees plaintext updates (no secure aggregation or DP, by design of the brief).
- vantage6 and Flower run without TLS, restricted by the cloud firewall. NVFlare uses its provisioned mTLS.
- One operator hosts all framework servers.
- Bit-exactness relies on identical CPUs and pinned library versions; all VMs are AMD EPYC-Rome.

## 2. Goal and assumptions

**Mission** (PROJECT_PLAN.md §1). Hospitals keep using their own framework, with no hub-specific code changes. One central hub runs plain FedAvg across all of them. The proof is one training run with 6 hospitals (2 per framework), logged to W&B and numerically equivalent to a framework-free FedAvg reference.

**Owner's assumptions, and whether they held:**

| Assumption | Held? |
|---|---|
| Data is good, code is correct, nobody is malicious, nothing breaks by itself: no anomaly detection, secure aggregation or DP | Held. The orchestrator still rejects malformed envelopes: wrong base model, wrong round, wrong tensor spec, non-finite values. |
| Minimal hospital changes: the standard framework client, plus connection settings | Held (§5). The only hub-specific hospital items are connection settings, credentials, the data path and, for vantage6, the operator's image in the node policy. The shared training library arrives as standard framework payload (NVFlare job, vantage6 image, Flower FAB) or as a pip dependency. |
| Security "reasonable for a test network" | Held. NVFlare uses mTLS, the firewall is limited to the 4 VM IPs, and secrets were never in git or on the bus in plaintext. |
| Plan's Flower port layout (ServerAppIo 9091, Control 9093) | **Did not hold.** Flower 1.39 has one SuperLink HTTP API (ServerApp + `flwr run`) on `--host/--port`. 9093 became that API and 9091 is unused (ADR-007). |
| vantage6 official algorithm base image usable | **Partly.** `algorithm-base:4.15` is Python 3.10. The image was rebuilt from `python:3.11.17-slim` with the same vantage6 tools (ADR-004). |
| vantage6 4.15 CLI usable as documented | **Partly.** On Python 3.11, str-Enum formatting bugs break `v6 server import` and `v6 server stop`; there are workarounds without patching vantage6 (ADR-005). |

## 3. Architecture as built

```
 central VM 145.38.205.58 (operator)
 ┌──────────────────────────────────────────────────────────────────────────────────────┐
 │ ORCHESTRATOR  hub/orchestrator     rounds · FedAvg (float64, site order) · eval · W&B│
 │ HUB CORE      hub/core 127.0.0.1:8800   sha256 blob store · model spec (systemd)     │
 └─────────▲──────────────────────────▲──────────────────────────▲──────────────────────┘
           │ flhub/0.1 JSON-RPC (localhost; weights by blob ref)
 ┌─────────┴─────────┐      ┌─────────┴─────────┐      ┌─────────┴─────────┐
 │ NVFlare adapter   │      │ vantage6 adapter  │      │ Flower adapter    │
 │ 127.0.0.1:8811    │      │ 127.0.0.1:8812    │      │ 127.0.0.1:8813    │
 │ RelayAdapter      │      │ BaseAdapter =     │      │ RelayAdapter      │
 │  ⇅ /relay/*       │      │ vantage6 user     │      │  ⇅ /relay/*       │
 │ relay job:        │      │ 'hub-adapter'     │      │ relay ServerApp   │
 │ HubRelayController│      │ (UserClient)      │      │ (long-lived run)  │
 │ (ModelController) │      │                   │      │                   │
 ├───────────────────┤      ├───────────────────┤      ├───────────────────┤
 │ NVFlare server    │      │ vantage6 server   │      │ Flower SuperLink  │
 │ :8002 fed (mTLS)  │      │ :7601 API         │      │ :9092 Fleet (gRPC)│
 │ :8003 admin       │      │ registry :5050    │      │ :9093 HTTP API    │
 └─────────▲─────────┘      └─────────▲─────────┘      └─────────▲─────────┘
 nvflare VM 145.38.189.142  vantage6 VM 145.38.186.63    flower VM 145.38.187.20
 NVFlare clients            vantage6 nodes (Docker)      Flower SuperNodes
 nvf-h1, nvf-h2             v6-h3, v6-h4                 flw-h5, flw-h6
 Client API script          algorithm image v6-train     ClientApp (FAB)
```

| Component | Host | Port | Code |
|---|---|---|---|
| agent bus (build time only) | central | 0.0.0.0:7700 | `tools/agentbus` |
| hub core | central | 127.0.0.1:8800 | `hub/core` |
| orchestrator, sim | central | — (CLI) | `hub/orchestrator`, `hub/sim` |
| NVFlare server + relay job; adapter | central | 8002 public, 8003 local; 127.0.0.1:8811 | startup kit; `adapters/nvflare` |
| vantage6 server; registry; adapter | central | 7601 public; 5050 public; 127.0.0.1:8812 | Docker `server:4.15.2`; `registry:2`; `adapters/vantage6` |
| Flower SuperLink + relay run; adapter | central | 9092 public, 9093 local; 127.0.0.1:8813 | `flower-superlink`; `adapters/flower`, `clients/flower` (relay ServerApp) |
| hospitals | framework VMs | outbound only | `clients/<fw>` + `flhub_core` |

**One round, step by step:**

1. The orchestrator serializes the global model (safetensors) and stores it in the hub core. The blob is identified by `sha256:<hex>`.
2. It calls `dispatch(round, model ref, train_config, sites)` on every adapter **in parallel** and polls `tasks/get`.
3. Each adapter fetches the model blob and hands it to its framework's own channel:
   - **NVFlare**: the relay job's `ModelController` gets the job by long-poll and calls `send_model_and_wait(FLModel(params, FULL, current_round))`. The clients run `flare.receive() → flhub_core.train → flare.send(FULL, meta num_examples)`.
   - **vantage6**: the adapter creates one task (`method train`, weights inline as base64 safetensors) for the two organisations. Each node pulls the operator's algorithm image and runs it on its CSV.
   - **Flower**: the relay ServerApp sends one TRAIN `Message` (`ArrayRecord` + `ConfigRecord`) per SuperNode through the Grid. Each ClientApp's `@app.train()` trains and replies with an `ArrayRecord` and a `MetricRecord`.
4. Every hospital runs `flhub_core.train(weights, its data, train_config + round)`. That function is deterministic (ADR-003), so the result does not depend on the framework that carried it.
5. Each adapter stores the returned weights in the hub core and builds one **envelope** per site: round, site, framework, base model, weights ref, `num_examples`, metrics, `train_config_hash`, client code version.
6. The orchestrator `collect`s and validates every envelope (§4). It then runs FedAvg `w = Σ n_k w_k / Σ n_k` in float64, in site-id order, cast to float32. Finally it evaluates on the central test split, logs to W&B and checkpoints `rounds/rNNNN.safetensors`.

## 4. The flhub/0.1 protocol

The full specification is `docs/agents/PROTOCOL.md`, frozen at 12:17 UTC with no changes afterwards. The reference implementation is `adapters/common/`.

- **Methods:**
  - `discover`: adapter, framework, version, sites with `online|offline|unknown`, capabilities.
  - `dispatch`: async, returns `task_id` in under 5 s.
  - `tasks/get`: state + progress + error.
  - `collect`: envelopes, idempotent.
  - `cancel`.
- **Errors:** -32010 unsupported protocol, -32011 unknown site, -32012 task not found, -32013 not completed, -32014 framework error (with the framework's text), -32015 adapter busy.
- **Client config:** dispatch `train_config` + `round`, handed unchanged to `flhub_core.train`.
- **Envelope rejection rules:**
  - protocol or round differs;
  - `base_model` ≠ the round's model;
  - unexpected site;
  - missing blob;
  - tensor names, shapes or dtypes ≠ the registered spec;
  - non-finite values;
  - `num_examples` ≤ 0;
  - `train_config_hash` (sha256 of canonical JSON) differs.

  A differing `client_code_version` is logged as a warning.
- **Hub core API:** PUT/GET/HEAD `/blobs/sha256:<hex>` (hash verified), POST/GET `/models/spec`, GET `/health`.
- **Relay pattern** (inside an adapter, not part of the protocol): `GET /relay/next` (long-poll), `POST /relay/result|progress|sites|shutdown`. It is used where the framework's server workflow must run inside the framework server (NVFlare, Flower).

**Mapping per framework, as built:**

| flhub | NVFlare 2.9.0 | vantage6 4.15.2 | Flower 1.39.0 |
|---|---|---|---|
| dispatch | relay `HubRelayController(ModelController).run()` → `send_model_and_wait(targets, FLModel(params, FULL, current_round, meta={train_config}))` | `client.task.create(collaboration, organizations, image, input_={"method":"train","kwargs":{weights_b64, train_config+round, sites}}, databases=[{"label":"default"}])` | relay ServerApp: `Message(RecordDict({"arrays": ArrayRecord, "config": ConfigRecord}), dst_node_id, MessageType.TRAIN)` via `Grid.push_messages/pull_messages` |
| site_id | NVFlare client name | organisation id → name (`sites` map in the task input) | `@app.query()` reply with `node_config["site-id"]` |
| num_examples | `FLModel.meta["num_examples"]` | result dict | `MetricRecord["num-examples"]` |
| weights back | `FLModel.params` (FULL, numpy) | base64 safetensors in the JSON result | `ArrayRecord` |
| sites online | relay view + FLARE API client job status | `client.node.list()` status | `Grid.get_node_ids()` + site-info QUERY |
| cancel | relay watcher calls `cancel_all_tasks()`; a late result (404) is ignored | `client.task.kill` | relay notices via `/relay/status`; a late reply (404) is ignored |

Changes during the build: none to the protocol. The only adjustment was in the plan: the Flower port map (ADR-007).

## 5. Hospital-side footprint per framework

This is the key claim of the project: each hospital runs its framework's standard client pattern, and the hub is invisible except for connection settings. Details and evidence: `docs/report/{nvflare,vantage6,flower}.md` §4.

| | NVFlare (nvf-h1, nvf-h2) | vantage6 (v6-h3, v6-h4) | Flower (flw-h5, flw-h6) |
|---|---|---|---|
| Installs | venv: `nvflare==2.9.0`, `torch==2.14.1+cpu`, numpy, safetensors, `flhub_core` | `vantage6==4.15.2` CLI (venv) + Docker; node image `ghcr.io/vantage6/infrastructure/node:4.15.2`. No torch on the host; `flhub_core` runs only inside the algorithm image (the simulation-only `~/venvs/flhub-data`, numpy + `flhub_core.data`, wrote the site CSVs) | venv: `flwr==1.39.0`, `torch==2.14.1+cpu`, numpy, safetensors, `flhub_core` |
| Configures | startup kit from the operator (server `flhub-central:8002`, mTLS certs); data path `~/flhub-data/<site>.csv` (default) | node YAML: `server_url http://145.38.205.58`, `port 7601`, `api_key`, database `uri` = the site CSV, policy `allowed_algorithms` = the operator's image; Docker `insecure-registries: 145.38.205.58:5050` | `flower-supernode --insecure --superlink 145.38.205.58:9092 --port 9094 --node-config "site-id='flw-h5' data-path='/home/<user>/flhub-data/flw-h5.csv'"` (flw-h6: `--port 9095`; values are TOML-quoted) |
| Code that runs | `clients/nvflare/client.py`: standard Client API loop (`flare.init/receive/send`) around `flhub_core.train` | algorithm `v6_flhub.train` (`@data(1)`, `@metadata`, `wrap_algorithm()`) around `flhub_core.train` | `clients/flower` ClientApp: `@app.train()` (ArrayRecord/ConfigRecord → `flhub_core.train` → ArrayRecord + MetricRecord), `@app.query()` site id |
| How it arrives | job custom code deployed by the NVFlare server (standard job deployment) | image pulled by the node from the operator's registry for each task (standard) | FAB shipped by `flwr run` from the SuperLink (standard) |
| Hub-specific items | connection (kit), data path | server address + API key, data path, registry + image policy | SuperLink address, node config (site id, data path) |

**Evidence:**
- Every hospital's envelopes carry `client_code_version 0.1.0+9c6b064c1e96`, the same as the hub's.
- The vantage6 host has no torch; its node venv `~/venvs/v6` holds vantage6 packages only, and `flhub_core` runs only inside the algorithm image (plus the simulation-only data venv `~/venvs/flhub-data` that created the site CSVs).
- The Flower hospital venv cannot import the hub package (`import adapters` fails).
- The NVFlare job code at both hospitals is byte-identical to `clients/nvflare/client.py` at the frozen SHA.

## 6. Data and model

- **Dataset** (ADR-001): PneumoniaMNIST from MedMNIST v2 (Yang et al. 2023, CC BY 4.0; source chest X-rays from Kermany et al. 2018). 28×28 grayscale, binary: normal (0) vs pneumonia (1).
  - File `pneumoniamnist.npz` from Zenodo record 10519652, 4,170,669 bytes, md5 `28209eda62fecd6e6a2d98b1501bb15f` (matches MedMNIST's), sha256 `e1792d3f03751cb101e99f19a63b3c1941436c988665f47853417b05be250cd8`.
  - Downloaded and checked by `flhub_core.data`.
- **Partitioning:** the official train split (4,708 images) is split into 6 IID partitions with `numpy.random.RandomState(2026).permutation` (a frozen stream). Each partition is written as one CSV (`label,x0..x783`, integer pixels) with a pinned sha256 per site.

  | Site | Framework | Partition | Examples | Pneumonia |
  |---|---|---|---|---|
  | nvf-h1 | NVFlare | 0 | 785 | 593 |
  | nvf-h2 | NVFlare | 1 | 785 | 569 |
  | v6-h3 | vantage6 | 2 | 785 | 567 |
  | v6-h4 | vantage6 | 3 | 785 | 584 |
  | flw-h5 | Flower | 4 | 784 | 591 |
  | flw-h6 | Flower | 5 | 784 | 590 |
  | test (central only) | — | official test split | 624 | 390 (62.5 %) |

- **Model:** a CNN with layers conv 1→8 (3×3, pad 1), ReLU, max-pool 2, conv 8→16 (3×3, pad 1), ReLU, max-pool 2, fc 784→64, ReLU, fc 64→2.
  - It has no normalization layers, so FedAvg has no buffer semantics.
  - 51,618 float32 parameters, 206 KB; a safetensors blob is 207,168 bytes.
  - The initial weights come from `torch.manual_seed(2026)`.
- **Training** (`configs/final.yaml`, frozen at b656577): 30 rounds, 1 local epoch, batch 64, SGD lr 0.05, momentum 0, seed 2026, fresh optimizer every round.
  - Determinism (ADR-003): one thread, `torch.use_deterministic_algorithms(True)`, and a data order seeded by sha256(seed, round, data fingerprint).
  - `python -m flhub_core.selfcheck` gave the same sha256 on all 4 VMs and inside the vantage6 image.
  - Tuning (sim, 30 rounds): lr 0.05 → 0.865 / AUC 0.925; lr 0.1 → 0.872 / 0.923; lr 0.2 → 0.861 / 0.919. The plan default was kept.

## 7. Experiments and results

All six final runs: W&B project `hpml_surf-surf/AgnosticFL`, tag `final`, group `final-2026-10-13`, git `b656577` (clean), state `finished`.

| Run | W&B id | URL | Rounds | Test acc | Test AUC | Test loss | Duration | s/round |
|---|---|---|---|---|---|---|---|---|
| centralized-baseline | j5gn5knm | https://wandb.ai/hpml_surf-surf/AgnosticFL/runs/j5gn5knm | 30 epochs | 0.8606 | 0.9279 | 0.5412 | 20 s | 0.6 |
| sim-fedavg-reference | kur37gs2 | https://wandb.ai/hpml_surf-surf/AgnosticFL/runs/kur37gs2 | 30 | 0.8654 | 0.9247 | 0.3497 | 20 s | 0.6 |
| nvflare-only | pavr8q6u | https://wandb.ai/hpml_surf-surf/AgnosticFL/runs/pavr8q6u | 30 | 0.8173 | 0.9224 | 0.4188 | 80 s | 2.5 |
| vantage6-only | twk49dhp | https://wandb.ai/hpml_surf-surf/AgnosticFL/runs/twk49dhp | 30 | 0.8638 | 0.9246 | 0.3631 | 238 s | 7.8 |
| flower-only | 011lasv0 | https://wandb.ai/hpml_surf-surf/AgnosticFL/runs/011lasv0 | 30 | 0.7580 | 0.9149 | 0.6225 | 221 s | 7.3 |
| mixed-6sites | iehjnc44 | https://wandb.ai/hpml_surf-surf/AgnosticFL/runs/iehjnc44 | 30 | **0.8654** | **0.9247** | 0.3497 | 244 s | 8.0 |

Test accuracy / AUC every 5 rounds (round 0 = the initial model):

| Round | centralized | sim-fedavg | nvflare-only | vantage6-only | flower-only | mixed-6sites |
|---|---|---|---|---|---|---|
| 0 | 0.625 / 0.649 | 0.625 / 0.649 | 0.625 / 0.649 | 0.625 / 0.649 | 0.625 / 0.649 | 0.625 / 0.649 |
| 5 | 0.780 / 0.916 | 0.625 / 0.644 | 0.625 / 0.609 | 0.625 / 0.648 | 0.625 / 0.611 | 0.625 / 0.644 |
| 10 | 0.861 / 0.923 | 0.625 / 0.809 | 0.625 / 0.780 | 0.625 / 0.820 | 0.625 / 0.775 | 0.625 / 0.809 |
| 15 | 0.819 / 0.920 | 0.651 / 0.915 | 0.800 / 0.923 | 0.744 / 0.921 | 0.639 / 0.918 | 0.651 / 0.915 |
| 20 | 0.811 / 0.920 | 0.713 / 0.912 | 0.657 / 0.905 | 0.532 / 0.921 | 0.630 / 0.898 | 0.713 / 0.912 |
| 25 | 0.878 / 0.928 | 0.861 / 0.923 | 0.848 / 0.921 | 0.732 / 0.910 | 0.873 / 0.928 | 0.861 / 0.923 |
| 30 | 0.861 / 0.928 | 0.865 / 0.925 | 0.817 / 0.922 | 0.864 / 0.925 | 0.758 / 0.915 | 0.865 / 0.925 |

The W&B runs also log per round: `global/test_{loss,acc,auc}`, `round/duration_s`, `round/num_sites`, `round/num_examples`, per site `site/<id>/{train_loss,train_acc,num_examples}`, and per framework `framework/<fw>/num_sites`. The config records the git SHA, framework versions, dataset and partition checksums, partition sizes, training config, client code versions and `deploy/versions.env`.

**Equivalence check** (`scripts/equivalence_check.py`, PROJECT_PLAN §6 P3):
- **Method:** load the global weights of both runs after every round (`rounds/rNNNN.safetensors`, rounds 0–30) and compute the max abs difference over all tensors; then compare the final test accuracy.
- **Pass criterion:** ≤ 1e-5 in every round and ≤ 0.1 pp.
- **Result for mixed-6sites vs sim-fedavg-reference:** max abs weight difference **0.000e+00 in every round 0–30**, final accuracy difference **0.0000 pp** → **PASS**.
  - The final global models are byte-identical (`sha256:7fd3e2a492c5…` in both summaries).
  - The result is stored in the mixed-6sites W&B summary (`equivalence/pass = true`, `equivalence/max_abs_weight_diff = 0`) and in `docs/report/evidence/equivalence-final.json`.
  - No ADR for a metric tolerance was needed.

**What the runs show:**
- **Framework-agnostic FedAvg is exact.** Routing the same round through NVFlare, vantage6 and Flower at once changes nothing numerically. Each framework carries float32 weights losslessly (NVFlare numpy FULL params, vantage6 base64 safetensors, Flower `ArrayRecord`), and the deterministic shared training code makes every hospital's update reproducible.
- **FedAvg over 6 sites ≈ centralized training.** Accuracy is 0.865 vs 0.861 and AUC 0.925 vs 0.928 after the same number of passes over the data. The federated runs plateau at the majority class (0.625) for about 13 rounds before accuracy rises, while AUC rises from round 1. Centralized training escapes earlier (more SGD steps per pass at the same lr), but both reach the same level.
- **Single-framework runs** use 2 sites and one third of the data. They reach a similar AUC (0.915–0.925), but accuracy at the 0.5 threshold fluctuates strongly from round to round (e.g. vantage6-only 0.53 at round 20, 0.86 at round 30). Their final accuracies (0.76–0.86) reflect this noise, not framework quality: the training code is identical everywhere.
- **Round latency comes from the framework, not the hub.** NVFlare takes 2.5 s/round (persistent job). vantage6 takes 7.8 s (one algorithm container and image check per task). Flower takes 7.3 s (one ClientApp process per message, torch import). Mixed takes 8.0 s, because rounds run in parallel and wait for the slowest framework.

## 8. How the agents built it

Four Claude agents (Opus 5.5 orchestrators with Haiku subagents), one per VM. They coordinated over the agent bus (`tools/agentbus`, port 7700) and committed to one GitHub repository as Zare2001. The full bus transcript is `docs/report/bus_transcript.md`.

**Timeline (UTC, 2026-10-13; `#n` = bus message):**

| Time | Event |
|---|---|
| 11:59–12:00 | all four agents up, bus up (#1–#4) |
| 12:00–12:08 | pins proposed by nvflare and vantage6; Flower 1.39 port change reported; API questions (#5–#8) |
| 12:10 | `flhub_core` pushed (c703faf, #9); API answers (#10) |
| 12:11–12:15 | `flhub_core.selfcheck` bit-exact on the flower, nvflare and vantage6 VMs and inside the vantage6 image (#11, #12, #14) |
| 12:17 | **M0-core**: adapters/common, hub core, protocol frozen (#15) |
| 12:19 / 12:25 | conformance suite (#18); orchestrator, sim, equivalence check (#19) |
| 12:28 | **M1-hub** (#20) |
| 12:31 / 12:35 / 12:44 | **M1-nvflare** (#24), **M1-vantage6** (#31), **M1-flower** (#45), each followed by a deploy-request |
| 12:34 / 12:42 / 12:46 | deploy-result PASS for nvflare (#29), vantage6 (#39), flower (#49); credentials as encrypted bus files #1–#3 |
| 12:38 / 12:46 / 12:50 | **M2-nvflare** (#36), **M2-vantage6** (#50), **M2-flower** (#54) |
| 12:51 | **M2-mixed** (#55); P3 freeze at b656577 (#56) |
| 12:52 | freeze confirmed by flower, nvflare, vantage6 (#59–#61) |
| 12:53–13:08 | six final runs online (`scripts/run_final.sh all`) |
| 13:09 | **M3-final** (#66); final-call (#67) |
| 13:11–13:13 | report-ready from vantage6, flower, nvflare (#71, #73, #75) |

**Per-agent sections** (full text in `docs/report/`):
- [`central.md`](docs/report/central.md): `flhub_core`, protocol, `adapters/common` (BaseAdapter, RelayAdapter), hub core, orchestrator, sim, conformance suite, scripts, central deployment, final runs.
- [`nvflare.md`](docs/report/nvflare.md): one long-running relay job (`HubRelayController(ModelController)`) kept alive by the adapter's FLARE API keeper (ADR-006); standard Client API script shipped as job custom code; mTLS provisioning with server name `flhub-central`.
- [`vantage6.md`](docs/report/vantage6.md): the adapter as a vantage6 user creating one task per round; the algorithm image on Python 3.11 (ADR-004); CLI workarounds (ADR-005); node policy and registry setup.
- [`flower.md`](docs/report/flower.md): relay ServerApp in a long-lived run; node-id → site-id mapping through a standard QUERY; persistent SuperLink and supervised SuperNodes (ADR-007).

**Collaboration statistics** (at the time of writing):
- 79+ commits, all authored and committed by Zare2001, each ≤ 400 changed lines. By scope: docs 17, deploy 15, flower 8, tests 7, report 7+, proto 5, orch 4, nvflare 4, core 3, v6 2, hub 2, scripts 1, chore 1.
- Bus messages up to final-call: 67+ — push 26, info 15, milestone 10, reply 7, deploy-request 3, deploy-result 3, secret 3, request 2, final-call 1, report-ready 3. By sender: central 24, flower 17, nvflare 15, vantage6 15.
- Deploy requests: 3, all PASS on the first attempt. Time from request to PASS: nvflare 2 min, vantage6 6 min (algorithm image build), flower 2 min.
- Every adapter passed the conformance suite against the central deployment on the first attempt.

**How subagents were used** (examples):
- central: a `runner` built the three framework venvs on the central VM with the pins (PASS, no resolver conflicts). `scribe` wrote the central worklog entries from facts supplied after each milestone.
- flower:
  - Three `scout`s in parallel read the *installed* flwr 1.39.0 (SuperLink/SuperExec flags, SuperNode/FAB install, `flwr run`). They found that ServerAppIo 9091 is gone, `--clientappio-api-address` is removed, connections live in `$FLWR_HOME/config.toml`, and the FAB has limits (bus #7, ADR-007).
  - One scout claim ("a SuperNode retries forever") was wrong. Island tests showed a SuperNode exits on UNAUTHENTICATED after an in-memory SuperLink restart, which led to `--database` and the restart loop.
  - `gitops` split the first slice into 5 path-scoped commits (661236f).
- vantage6:
  - Three `scout`s read the installed vantage6 4.15.2 source (CLI, client and algorithm tools, plus the node source inside the node image). They found that `v6 server/node new` is interactive only, the node image tag rule, the `require_algorithm_pull` default, that results come back as JSON strings, and that the official base image is py3.10 (ADR-004).
  - `gitops` made 14 commits in 7 runs with exact file lists and secret-scanned the report before pushing.
- nvflare:
  - Two `scout`s read the installed NVFlare 2.9.0 FLARE API and provisioning code and produced a 15-line API summary in the worklog.
  - The two critical pitfalls were found by the agent's own verification: the 2.9 class allow-list with its BYOC bypass, and absolute-path `ScriptRunner`.
  - `gitops` made 18 commits in 9 pushes and recovered once from a stale origin ref.
- The framework agents did not use `runner` or `scribe`: island debugging needed live interaction, and they wrote reports from first-hand evidence.
- Lesson: subagent findings were treated as leads and verified against the installed code or by tests.

## 9. Problems encountered and how they were solved

The full tables are in the per-agent sections. These were the most instructive:

| Problem | Component | Symptom | Root cause | Fix | Commit | Time lost |
|---|---|---|---|---|---|---|
| Package shadowed | flhub_core | `cannot import name … (unknown location)` from the repo root | setuptools editable finder runs after PathFinder; outer dir = namespace package | src layout → `.pth` path entry | 635a31b | 5 min |
| Plan ports did not exist | Flower SuperLink | no ServerAppIo / ClientAppIo flags | Flower 1.39: one HTTP API per SuperLink/SuperNode | 9093 = HTTP API, 9091 unused, SuperNode `--port 9094/9095` (ADR-007) | 7f5adec | — |
| SuperNodes vanish after SuperLink restart | Flower | "SuperNode terminated gracefully" | in-memory SuperLink forgets node ids → UNAUTHENTICATED → SuperNode exits | `--database` + `supernode.sh` restart loop | 7f5adec | — |
| Stale node id after a SuperNode restart | Flower relay | old id still listed online | heartbeat-based offline tagging | non-blocking site-info QUERY; newest node per site wins | ccc26dc | — |
| `v6 server import` loses its DB | vantage6 CLI on py3.11 | "No root user found" | str-Enum f-string gives the env var `ServerGlobals.DB_URI_ENV_VAR` | run the image's `vserver-local import` with `VANTAGE6_DB_URI` (ADR-005) | 31b5f3d | 15 min |
| Server not stoppable | vantage6 CLI | "flhub is not running!" | same Enum bug in the container name | stop by Docker label | 31b5f3d | 5 min |
| Python 3.10 base image | vantage6 algorithm | plan pins py3.11 + identical torch | official base is `python:3.10-slim` | own image FROM `python:3.11.17-slim-bookworm` (ADR-004) | 30eccd3 | 5 min |
| Wrong client code path | NVFlare job | clients ran the build machine's absolute path | `ScriptRunner` keeps absolute paths | build the job with a relative script path | 13a1650 | 10 min |
| Stopped hospital shown online | NVFlare adapter | online 90 s after the client stopped | long heartbeat timeout | `get_client_job_status` probe | 4c697bf | 15 min |
| Restarted hospital never trains | NVFlare | site stays offline | clients do not rejoin a running job | keeper restarts the relay job gracefully (ADR-006) | 4c697bf | 15 min |
| Own relay protocol (dead end) | NVFlare, Flower | duplicate of `adapters.common` relay | relays prototyped before M0-core landed | ported to `RelayAdapter`/`RelayClient` | 97cc459, 7f5adec | 20 min (nvflare) |
| `pkill -f` killed the calling shell | ops (3 agents) | exit 144 | the pattern also matched the shell's own command line | kill by pid file / process name only | — | minutes |
| Status exit codes inconsistent | deploy | status 0 with sites offline | different conventions | every `status.sh` exits 1 unless all sites are online | 9c88bca, 8b19ca5 | 5 min |
| Root pytest failed on collection | tests | `No module named 'flwr'` in the hub venv | Flower test imported flwr unconditionally | `pytest.importorskip("flwr")` | 6626cb3 | 2 min |

## 10. Decisions (docs/DECISIONS.md)

| ADR | Decision | Outcome |
|---|---|---|
| ADR-001 | PneumoniaMNIST, 6 IID CSV partitions with pinned sha256, small CNN | 0.865 acc / 0.925 AUC federated, 0.861 / 0.928 centralized |
| ADR-002 | Version pins: py3.11.17, torch 2.14.1+cpu, numpy 2.4.4, NVFlare 2.9.0, vantage6 4.15.2, flwr 1.39.0 | identical stack on every VM and in the image |
| ADR-003 | Data order seeded by (seed, round, data fingerprint); 1 thread; deterministic algorithms | bit-exact training across all VMs and frameworks; no hospital config |
| ADR-004 | vantage6 algorithm image on `python:3.11.17-slim` with the same vantage6 tools as the official base | standard algorithm; same selfcheck hash as the VMs |
| ADR-005 | vantage6 4.15.2 CLI workarounds on Python 3.11 (import, stop, root user, log dir) | no patched vantage6 code |
| ADR-006 | NVFlare relay = one long-running job kept alive by the adapter; restart on client reconnect | one job served every P2/P3 run |
| ADR-007 | Flower 1.39: HTTP API on 9093, persistent SuperLink DB, supervised SuperNodes, no TLS (firewall) | stable SuperNode ids across restarts |

## 11. Reproduce from scratch

Versions (`deploy/versions.env`): Python 3.11.17, torch 2.14.1+cpu (CPU index), numpy 2.4.4, safetensors 0.8.0, NVFlare 2.9.0, vantage6 4.15.2 (server/node images `ghcr.io/vantage6/infrastructure/{server,node}:4.15.2`), flwr 1.39.0, wandb 0.30.0, `registry:2`, algorithm base `python:3.11.17-slim-bookworm`. Ports and IPs: `deploy/topology.env` and §3.

1. **Owner, once:**
   - Four Ubuntu 24.04 VMs; `/etc/hosts` maps `flhub-central` to 145.38.205.58 (127.0.0.1 on central).
   - Cloud firewall: open 7700 (all three VMs), 8002 (NVFlare VM), 7601 + 5050 (vantage6 VM) and 9092 (Flower VM) on the central VM, from those IPs only.
   - Run `bootstrap_vm.sh <role>` from the agent kit (`docs/agents/README_SETUP.md`). It installs git, uv, Docker and the agent bus, and fills `~/.flhub/secrets.env`.
2. **Central VM:**
   ```bash
   cd ~/AgnosticFL
   bash deploy/central/install.sh && bash deploy/central/start.sh          # hub venv, dataset, registry :5050, hub core :8800
   bash deploy/central/nvflare/install.sh && bash deploy/central/nvflare/start.sh
   tools/bus put ~/flhub-runtime/nvflare/server/kits/nvf-h1.tgz --to nvflare --secret   # same for nvf-h2
   bash deploy/central/vantage6/install.sh && bash deploy/central/vantage6/start.sh   # builds + pushes the algorithm image
   tools/bus put ~/flhub-runtime/vantage6/v6-nodes.env --to vantage6 --secret
   bash deploy/central/flower/install.sh && bash deploy/central/flower/start.sh
   ```
3. **NVFlare VM:**
   ```bash
   bash deploy/nvflare-vm/install.sh
   tools/bus get <id> -o ~/flhub-runtime/nvflare/incoming/nvf-h1.tgz   # same for nvf-h2
   bash deploy/nvflare-vm/install.sh && bash deploy/nvflare-vm/start.sh && bash deploy/nvflare-vm/status.sh
   ```
4. **vantage6 VM:**
   ```bash
   tools/bus get <id> -o /tmp/v6-nodes.env
   bash deploy/vantage6-vm/install.sh --keys /tmp/v6-nodes.env   # adds insecure-registries, restarts Docker
   bash deploy/vantage6-vm/start.sh && bash deploy/vantage6-vm/status.sh
   ```
5. **Flower VM** (no credentials):
   ```bash
   bash deploy/flower-vm/install.sh && bash deploy/flower-vm/start.sh && bash deploy/flower-vm/status.sh
   ```
6. **Central VM, check and run:**
   ```bash
   bash deploy/central/status.sh                     # HEALTHY = bus, hub core, registry and all 6 sites online
   for p in 8811 8812 8813; do ~/venvs/hub/bin/python -m pytest tests/conformance --adapter http://127.0.0.1:$p -v -s; done
   bash scripts/run_final.sh all                     # 6 runs online to W&B (tag final) + equivalence check
   ~/venvs/hub/bin/python scripts/wandb_cleanup.py --dedupe && ~/venvs/hub/bin/python scripts/wandb_cleanup.py --dedupe --apply
   ```
   Rough estimate from this build: about 30 minutes of script time from fresh VMs. That covers venv installs, the ~2-minute vantage6 image build, and ~15 minutes for the six final runs.

## 12. Definition of Done — evidence

Checked on 2026-10-13 between 13:15 and 13:20 UTC. The output below is trimmed.

**1. All services start from `deploy/` scripts; `status.sh` on each VM reports healthy.**
```
central  $ bash deploy/central/status.sh   (13:15:19Z, exit 0)
  agent bus :7700 OK | hub core :8800 OK | registry :5050 OK {"repositories":["flhub/v6-train"]}
  nvflare  OK: server up (flhub-central, fed :8002, admin :8003); adapter 127.0.0.1:8811; nvf-h1 online, nvf-h2 online
  vantage6 OK: server {"version": "4.15.2"} on :7601; image 145.38.205.58:5050/flhub/v6-train:bd800cee7922; v6-h3=online v6-h4=online
  flower   OK: superlink fleet 0.0.0.0:9092, HTTP 127.0.0.1:9093; adapter :8813; relay polling; flw-h5=online, flw-h6=online
  HEALTHY
nvflare  $ bash deploy/nvflare-vm/status.sh   (13:15:24Z, exit 0)
  nvf-h1: up (connected to flhub-central:8002 = 145.38.205.58, job processes: 1)   nvf-h2: up (same)   status: HEALTHY
vantage6 $ bash deploy/vantage6-vm/status.sh  (13:15:23Z, exit 0)
  v6-h3 OK connected node:4.15.2 -> http://145.38.205.58:7601 (central)   v6-h4 OK connected (same)
flower   $ bash deploy/flower-vm/status.sh    (13:15:24Z, exit 0)
  flw-h5: running, connected to 145.38.205.58:9092   flw-h6: running, connected to 145.38.205.58:9092   flower-vm: HEALTHY
```

**2. Hospital clients run the frameworks' standard client patterns; only connection settings and credentials are hub-specific.**
- Each framework agent verified this on its VM; see §5 and `docs/report/<fw>.md` §4.
- The hospital venvs contain only the framework, torch, numpy, safetensors and `flhub_core`. `import adapters` / `import hub` fail there, and the vantage6 host has no torch.
- Training code arrives as standard framework payload: the NVFlare job's `custom/client.py` (byte-identical to the repo), the vantage6 image from the operator's registry, and the Flower FAB via `flwr run`.
- Every envelope carries `client_code_version 0.1.0+9c6b064c1e96`.

**3. Every adapter passes the conformance suite against the central deployment.**
```
pytest tests/conformance --adapter http://127.0.0.1:8811  -> 7 passed in 13.7 s (NVFlare;  max_abs_diff 0.0 rounds 1, 2, 4)
pytest tests/conformance --adapter http://127.0.0.1:8812  -> 7 passed in 26.4 s (vantage6; max_abs_diff 0.0 rounds 1, 2, 4)
pytest tests/conformance --adapter http://127.0.0.1:8813  -> 7 passed in 30.2 s (Flower;   max_abs_diff 0.0 rounds 1, 2, 4)
```
Reports: `docs/report/evidence/conformance-{nvflare,vantage6,flower}-central.json`.

**4. `mixed-6sites` completed all rounds with all 6 sites in every round.**
```
rounds logged: 30 | num_sites per round: [6] | (nvflare, vantage6, flower) sites per round: [(2, 2, 2)]
every round has all 6 sites: True | client code versions: {'0.1.0+9c6b064c1e96'} | W&B iehjnc44 state=finished
```

**5. The equivalence check passed.**
```
python scripts/equivalence_check.py ~/flhub-runtime/runs/mixed-6sites-latest ~/flhub-runtime/runs/sim-fedavg-reference-latest
max abs weight diff over all rounds: 0.000e+00 (tol 1e-05) -> PASS     (rounds 0-30, every round 0.000e+00)
final test acc diff: 0.0000 pp (tol 0.1) -> PASS
EQUIVALENCE: PASS          (also in W&B summary of iehjnc44: equivalence/pass = true)
```

**6. The W&B project contains exactly the final runs, all finished and tagged `final`.**
```
python scripts/wandb_cleanup.py --dedupe --apply
project hpml_surf-surf/AgnosticFL: 6 runs, keep 6, delete 0
  KEEP mixed-6sites iehjnc44 | flower-only 011lasv0 | vantage6-only twk49dhp | nvflare-only pavr8q6u |
       sim-fedavg-reference kur37gs2 | centralized-baseline j5gn5knm   (state=finished, tags=['final'], group final-2026-10-13)
```
All P1/P2 runs used `--wandb offline` or `disabled`. The W&B project did not exist at 12:29 UTC (checked through the W&B API) and was created by the first final run.

**7. `main` is green; every commit is by Zare2001 with no AI attribution.**
```
~/venvs/hub     pytest                                   -> 28 passed, 9 skipped (conformance needs --adapter; framework tests skip without the framework)
~/venvs/flower  pytest tests/flower                      -> 5 passed
~/venvs/nvflare pytest tests/unit/test_nvflare_adapter.py -> 7 passed
~/venvs/v6      pytest tests/unit/test_vantage6_adapter.py -> 5 passed
git log --format='%an|%cn|%B' | grep -Ei 'co-authored|claude|anthropic'   -> (nothing)
git log --format='%an|%cn' | sort | uniq -c                               -> all commits Zare2001|Zare2001
```

**8. Reports are pushed:** `REPORT.md`, `README.md`, `docs/DECISIONS.md` (ADR-001..007), `docs/report/{central,nvflare,vantage6,flower}.md`, `docs/report/bus_transcript.md` and `docs/report/evidence/`.

**9. all-done and DONE files:** the central agent broadcasts `all-done` after pushing this report. Each agent verifies its DoD items, writes its final worklog entry and creates `~/.flhub/DONE`; the confirmations are at the end of the bus transcript.

## 13. Limitations, security notes and next steps

**Not production-ready:**
- **Plaintext updates at the hub.** The orchestrator, hub core and adapters see every site's weights. There is no secure aggregation and no differential privacy (out of scope by the owner's assumptions). Weights are also stored unencrypted in the vantage6 server database (collaboration encryption is off).
- **Transport security is mixed.**
  - NVFlare uses its provisioned mTLS (root CA `CN=flhub`, kits valid until 2027-10-08).
  - vantage6 (:7601) and the registry (:5050) are plain HTTP.
  - Flower (:9092) runs `--insecure` without SuperNode authentication.
  - The test network relies on the cloud firewall (only the four VM IPs).
- **Single operator.** One VM hosts the hub and all three framework servers. Whoever holds it (and the NVFlare admin kit, the vantage6 root account, the Flower SuperLink) controls every federation.
- **No anomaly checks** beyond envelope validation (spec, finite values, round, base model, config hash). Malicious or broken sites are not detected (owner's assumption).
- **Bit-exactness is conditional.** It needs identical CPU code paths and pinned versions. On mixed hardware, fall back to the CPU-feature pinning in ADR-003 or the metric tolerance.
- **Latency** is dominated by framework mechanics: vantage6 starts one container per task (~8 s/round), Flower one ClientApp process per message (~7 s). Fine for 30 rounds, not for thousands.
- **Operations.** Watchdogs are cron jobs, logs are local files, and there is no monitoring. The vantage6 4.15 CLI needs workarounds on Python 3.11 (ADR-005).

**Recommended next steps:**
1. **Secure aggregation.** Move aggregation into MPC or threshold HE between the adapters, so the hub only sees the aggregate (protocol `flhub/0.2`: masked updates and an `aggregate` step). Add DP noise at the clients via `flhub_core`.
2. **Close the transport gaps.** Use Flower TLS + `--enable-supernode-auth`, an HTTPS reverse proxy for vantage6, a registry with TLS and auth, and pin the vantage6 image by digest. Bind NVFlare admin to loopback.
3. **More frameworks through the same protocol.** BranchKey or others: implement `BaseAdapter`, pass `tests/conformance`, add deploy scripts (§14).
4. **Federated evaluation** (`capabilities.evaluate`), and partial participation with stragglers (today every site must answer).
5. **vantage6 5.x on Kubernetes,** and separate operators per framework server.
6. **A real hospital pilot** with non-IID data, data-quality checks and a governance model for the hub operator.

## 14. How to add a new framework adapter

1. **Understand the contract.** Read `docs/agents/PROTOCOL.md` (five JSON-RPC methods, the envelope, error codes) and `adapters/mock/service.py`, the smallest complete adapter (60 lines).
2. **Write the adapter** in `adapters/<fw>/service.py`, on the central/operator side only:
   - If your framework lets an external client start work (like vantage6's user API), subclass `adapters.common.BaseAdapter` and implement:
     - `site_status() -> {site_id: "online"|"offline"|"unknown"}`
     - `run_round(task, weights, client_config) -> [SiteResult(site_id, num_examples, metrics, client_code_version, weights=...)]`
   - If the server-side workflow must run inside the framework server (like NVFlare's ModelController or Flower's ServerApp), subclass `RelayAdapter`. In the framework's server workflow, loop over `RelayClient.next_job()` → send `job["config"]` and the weights to `job["sites"]` → `post_result(...)` (see `adapters/common/relay.py`).
   - The base class handles protocol checks, error codes, deadlines, cancel, hub-core blobs and envelopes.
3. **Keep the hospital side standard.** Use the framework's documented client pattern. Inside it, call `flhub_core.train.train(weights, flhub_core.data.load_site(path_or_df), client_config)` and return the weights (float32, `state_dict` names), `num_examples`, `metrics` and `flhub_core.code_version()`. The only hub-specific items should be connection settings, credentials and the data path. Ship the code the framework's way (job, image, app bundle).
4. **Check determinism.** `python -m flhub_core.selfcheck` on the hospital machine must print the same `result` hash as the hub.
5. **Pass the admission test** with `pytest tests/conformance --adapter http://127.0.0.1:<port> -v -s --report conformance-<fw>.json`. It requires round 1 to equal direct `flhub_core.train` (≤ 1e-6), correct error codes, busy handling, and recovery after cancel.
6. **Add deploy scripts and a config entry.** Scripts: `deploy/central/<fw>/{install,start,stop,status}.sh` (status exits 1 unless all sites are online) and `deploy/<fw>-vm/...` for the hospitals. Add one entry under `adapters:` in `configs/final.yaml`, with a free local port and the site list.
7. **Run it.** `python -m hub.orchestrator run --config configs/final.yaml --only <fw> --rounds 3 --wandb offline`, then the mixed run; check `scripts/equivalence_check.py` against `hub.sim`. Nothing in the orchestrator, hub core or protocol changes.

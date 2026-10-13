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
 ┌──────────────────────────────────────────────────────────────────────────────────┐
 │ ORCHESTRATOR  hub/orchestrator     rounds · FedAvg (float64, site order) · eval · W&B│
 │ HUB CORE      hub/core 127.0.0.1:8800   sha256 blob store · model spec (systemd)     │
 └─────────▲──────────────────────────▲──────────────────────────▲────────────────────┘
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
| cancel | relay job continues; late result ignored | `client.task.kill` | late reply ignored (404) |

Changes during the build: none to the protocol. The only adjustment was in the plan: the Flower port map (ADR-007).

## 5. Hospital-side footprint per framework

This is the key claim of the project: each hospital runs its framework's standard client pattern, and the hub is invisible except for connection settings. Details and evidence: `docs/report/{nvflare,vantage6,flower}.md` §4.

| | NVFlare (nvf-h1, nvf-h2) | vantage6 (v6-h3, v6-h4) | Flower (flw-h5, flw-h6) |
|---|---|---|---|
| Installs | venv: `nvflare==2.9.0`, `torch==2.14.1+cpu`, numpy, safetensors, `flhub_core` | `vantage6==4.15.2` CLI (venv) + Docker; node image `ghcr.io/vantage6/infrastructure/node:4.15.2`. No torch or flhub_core on the host | venv: `flwr==1.39.0`, `torch==2.14.1+cpu`, numpy, safetensors, `flhub_core` |
| Configures | startup kit from the operator (server `flhub-central:8002`, mTLS certs); data path `~/flhub-data/<site>.csv` (default) | node YAML: `server_url http://145.38.205.58`, `port 7601`, `api_key`, database `uri` = the site CSV, policy `allowed_algorithms` = the operator's image; Docker `insecure-registries: 145.38.205.58:5050` | `flower-supernode --superlink 145.38.205.58:9092 --node-config "site-id=… data-path=…"` |
| Code that runs | `clients/nvflare/client.py`: standard Client API loop (`flare.init/receive/send`) around `flhub_core.train` | algorithm `v6_flhub.train` (`@data(1)`, `@metadata`, `wrap_algorithm()`) around `flhub_core.train` | `clients/flower` ClientApp: `@app.train()` (ArrayRecord/ConfigRecord → `flhub_core.train` → ArrayRecord + MetricRecord), `@app.query()` site id |
| How it arrives | job custom code deployed by the NVFlare server (standard job deployment) | image pulled by the node from the operator's registry for each task (standard) | FAB shipped by `flwr run` from the SuperLink (standard) |
| Hub-specific items | connection (kit), data path | server address + API key, data path, registry + image policy | SuperLink address, node config (site id, data path) |

**Evidence:**
- Every hospital's envelopes carry `client_code_version 0.1.0+9c6b064c1e96`, the same as the hub's.
- The vantage6 host has no torch and no flhub_core (`~/venvs/v6`: vantage6 packages only).
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

# Support matrix

What was tested, with which versions, and what is not supported. "Tested" means: on the reference testbed (four Ubuntu 24.04 VMs, CPU-only, one data centre), in the test stages of [results](results/README.md).

## Frameworks and tested versions

| | NVFlare | vantage6 | Flower |
|---|---|---|---|
| Version | 2.9.0 | 4.15.2 (server and node images `ghcr.io/vantage6/infrastructure/{server,node}:4.15.2`) | 1.39.0 |
| Tested in | stage 1 (`flhub/0.1`) and stage 2 (`flhub/0.2`) | stage 1 and stage 2 | stage 1 and stage 2 |
| Adapter | `RelayAdapter`: one long-running job, one NVFlare task per site | `BaseAdapter`: a vantage6 user, one task per site and round | `RelayAdapter`: a ServerApp in a long-lived run, one message per site |
| Hospital client | Client API script | algorithm image (`@data`, `@metadata`, `wrap_algorithm()`) | ClientApp in the FAB |
| Rollout of new training code | job custom code; once: remove an installed `flhub_core`, 1 client restart | algorithm image tag; 0 steps | FAB; 0 steps |
| Conformance suite v2 against the deployment | 13 / 13 | 13 / 13 | 13 / 13 |
| Transport security on the testbed | mTLS (provisioned kits) | plain HTTP, node API keys | `--insecure` |
| Sites tested | 2, and 4 in the 12-site test | 2, and 4 | 2, and 4 |
| Largest model update carried | 512 MiB (1024 MiB on a single-host test) | 128 MiB | 512 MiB |

Sources: the framework guides ([NVFlare](frameworks/nvflare.md), [vantage6](frameworks/vantage6.md), [Flower](frameworks/flower.md)), conformance reports `conformance-v2-*-central.json` in [results/evidence/stage2/](results/evidence/stage2/), [tables.md](results/evidence/stage2/tables.md). Other framework versions were not tested; framework APIs change between versions, so check the adapter's framework calls (listed in each guide's §5.2) before an upgrade.

## Runtime versions

| Component | Version |
|---|---|
| Python | 3.11.17 |
| torch | 2.14.1 (CPU build, from the PyTorch CPU index) |
| numpy, safetensors, PyYAML | 2.4.4, 0.8.0, 6.0.3 |
| W&B (optional, for uploading finished runs) | 0.30.0 |
| Operating system, CPU | Ubuntu 24.04, x86_64 with AVX2 (AMD EPYC-Rome) |
| Containers (vantage6 nodes, image registry) | Docker; `registry:2`; algorithm image based on `python:3.11.17-slim-bookworm` |

All pins: [`deploy/versions.env`](../deploy/versions.env). Bit-exact results across machines need the same versions and the same CPU code paths (`python -m flhub_core.selfcheck` prints the same digest).

## Protocol versions

| Version | Status | Content |
|---|---|---|
| `flhub/0.1` | frozen 2026-10-13 | `discover`, `dispatch`, `tasks/get`, `collect`, `cancel`; envelopes; hub-core API |
| `flhub/0.2` | stage 2 | optional fields only: per-site progress, partial collect, per-site cancel, per-run readiness, data-variant hint, payloads ≥ 1024 MiB |

A `flhub/0.2` adapter answers `flhub/0.1` callers in their version; the orchestrator negotiates the version with `discover` ([protocol](protocol.md)).

## Features

| Feature | Status | Notes |
|---|---|---|
| Aggregators | FedAvg, FedAvgM, FedAdam, FedYogi, FedAdagrad, FedProx | float64 state, checkpointed ([aggregators](aggregators.md)) |
| Partial participation | yes | seeded sampling of a fraction of the sites per round |
| `min_sites` and deadlines | yes | late sites cancelled per site; the round goes on with the rest |
| Per-site cancel | yes, all three adapters | NVFlare cannot stop a cancelled site's local training (it finishes and is dropped) |
| Retries of failed rounds | yes | a retry trains from the same model and gives the same result |
| Resume after a hub crash | yes, bit-exact | including the aggregator state |
| Data variants | yes | files next to the site's data (NVFlare, Flower) or labelled node databases (vantage6) |
| Mixed federations | yes | all three frameworks in one run; the round runs at the slowest framework's pace |
| Sites | up to 12 tested (4 per framework) | |
| Models tested | CNNs with 52 k and 1.7 M parameters, ResNet-18 with GroupNorm (11.2 M), synthetic payloads up to 512 MiB | float32 parameters, no BatchNorm ([`flhub_core/`](../flhub_core/)) |
| Datasets | PneumoniaMNIST, OrganAMNIST (MedMNIST v2), as CSV files per site | other data needs a loader in `flhub_core.data` |
| Evaluation | on the hub's own test or validation split, every round or every k rounds | not at the sites |
| Equivalence with a framework-free simulation | yes, hash by hash per round | `hub.sim --compare`, `--replay` |
| Code-version check | yes | every update reports `client_code_version`; `strict_code_version` rejects others |
| Run records | run directories with per-round hashes; upload to W&B after the equivalence check | |

## Measured limits per framework

| Framework | Limit |
|---|---|
| NVFlare | about 2.5 s fixed cost per round (`task_request_interval`, 2 s by default); a crash of one site's training process ends the round for every NVFlare site (one relay job serves them all); a restarted client rejoins only when the relay job is restarted between rounds; a cancelled task keeps training at the hospital; a dead client is failed after 30 s offline |
| vantage6 | with the default SQLite server, 256 MiB updates are never delivered and time grows superlinearly above 64 MiB (468 s per round at 128 MiB); about 536 MiB is the largest storable model; 5–9× the payload on the wire; about 6.3 s fixed cost per round; the overhead grows while tasks stay in the server database; nodes never delete task folders; only unencrypted collaborations |
| Flower | about 5.5 s fixed cost per round; the relay run ends with the SuperLink (the adapter starts a new one, the round in flight is retried); the SuperLink database keeps about 4× the payload until it is purged; the FAB, which carries `flhub_core`, is limited to 10 MiB |
| Testbed (not frameworks) | 19 GB disk: 1024 MiB updates (single framework) and 256 MiB (three frameworks) did not fit; CPU-only training: ResNet-18 ran 20 rounds |

Details, causes and knobs: each guide's §6 ([NVFlare](frameworks/nvflare.md#6-limits-known-issues-and-knobs), [vantage6](frameworks/vantage6.md#6-limits-known-issues-and-knobs), [Flower](frameworks/flower.md#6-limits-known-issues-and-knobs)).

## Not supported

- Secure aggregation and differential privacy: the hub sees every update in plaintext ([trust and security model](architecture.md#trust-and-security-model)).
- Robust aggregation against malicious updates (median, trimmed mean, Krum) and methods with client-side control variates (SCAFFOLD).
- Federated evaluation at the sites, personalization, asynchronous rounds.
- GPUs: training is CPU-only and single-threaded by design (determinism).
- More than one run at a time on the same sites: an adapter refuses a dispatch for a site that is still busy (`-32015`).
- Encrypted vantage6 collaborations, and TLS for vantage6 and Flower: not tested (vantage6 encryption needs an adapter change).
- Other framework versions (for example vantage6 5.x), other operating systems, the hub and the framework servers on separate hosts: not tested.

What would lift these limits: [limitations and future work](limitations-and-future-work.md).

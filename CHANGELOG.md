# Changelog

All notable changes to this project. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow [Semantic Versioning](https://semver.org/). The protocol has its own version (`flhub/0.1`, `flhub/0.2`, see [docs/protocol.md](docs/protocol.md)).

## [0.2.1] - 2026-10-14

Examples for using the hub with each framework and for adding a framework. The hub, the adapters, `flhub_core` and the clients are unchanged.

### Added

- `examples/`: per framework (NVFlare, vantage6, Flower) a minimal hospital-side client of the client contract, with every line marked as the framework's pattern or the hub's addition, and the server side as thin annotated code over the library; each runs on one machine without a deployment (NVFlare's simulator, vantage6's `MockAlgorithmClient`, a local Flower SuperLink with two SuperNodes).
- `examples/new-framework/`: a skeleton adapter in the direct (`BaseAdapter`) and in the relay (`RelayAdapter`) pattern on a toy framework, a client stub, and `check.sh`, which runs the conformance suite against both and three orchestrator rounds with the equivalence check.
- CI runs `examples/new-framework/check.sh`.

## [0.2.0] - 2026-10-14

The second test stage (full system and performance test) and the restructure of the repository into a documented library.

### Added

- Protocol `flhub/0.2`, backward-compatible: per-site progress, partial collect, per-site cancel, per-run readiness, a data-variant hint and payloads of at least 1024 MiB; hub-core blob deletion and garbage collection.
- Server aggregators `fedavgm`, `fedadam`, `fedyogi`, `fedadagrad` and `fedprox` next to `fedavg`, with float64 state that is checkpointed every round.
- Orchestrator: configuration schema 2 with a site topology, seeded partial participation, `min_sites` with deadlines, round retries, bit-exact `--resume` after a crash, per-round hashes of the global weights and the aggregator state, a timing breakdown per round, site-shaped evaluation.
- Simulation (`hub.sim`): parallel workers, replay of a real run's rounds, comparison with a real run (`--compare`), recorded participants, centralized and local-only references.
- `flhub_core` 0.2: OrganAMNIST, deterministic partitions with manifests, data variants, models `cnn-medium`, `resnet18-gn` and `payload-<N>`, FedProx μ, weight decay, timing and memory metrics, data fingerprints, selfcheck v2.
- Native FedAvg baselines for NVFlare, vantage6 and Flower, for the overhead comparison.
- Conformance suite v2 (13 tests): subset dispatch, partial collect with per-site cancel, offline sites, adapter restart, payload echo, data variants.
- Testbed tooling: payload sweep, bandwidth emulation, fault injection, CPU-throttled stragglers, extra sites, system monitor, run finalization with an equivalence gate.
- Documentation: getting started, architecture with a trust and security model, client contract, a guide per framework, adding a framework, aggregators, deployment, reproducing results, support matrix, cost and efficiency, limitations and future work; condensed results of both stages with figures and evidence; documentation tooling in `scripts/docs/`.

### Changed

- Adapters moved to `flhub/0.2`: NVFlare sends one task per site from its relay job; vantage6 creates one task per site and round and selects data variants by database label; Flower's relay posts a liveness beat and is relaunched when it is lost.
- The orchestrator writes a run directory only; runs are uploaded to W&B after their equivalence check passed (`scripts/stage2/finalize_run.py`).
- New `flhub_core` versions reach the hospitals through each framework's own channel: NVFlare job custom code, a vantage6 algorithm image tag, the Flower FAB.
- Repository layout: the protocol moved to `docs/protocol.md`, the decision records to `docs/design/decisions.md`, results and evidence to `docs/results/`. The full stage reports and the material of the automated build are kept on branch `archive/agentic-build` and at the tags `stage1-final` and `stage2-final`.

## [0.1.0] - 2026-10-13

The first test stage: one FedAvg hub across NVFlare, vantage6 and Flower.

### Added

- Hub core: a content-addressed blob store for model weights and the model's tensor spec.
- Orchestrator: rounds, envelope validation, weighted FedAvg in float64 with a fixed site order, global evaluation, checkpoints, W&B logging.
- Framework-free FedAvg simulation and centralized baseline (`hub.sim`), and an equivalence check between runs.
- Protocol `flhub/0.1` (JSON-RPC): `discover`, `dispatch`, `tasks/get`, `collect`, `cancel`, with error codes and the envelope format.
- `adapters/common`: `BaseAdapter`, `RelayAdapter` and `RelayClient`, the JSON-RPC server and the hub-core client.
- Adapters for NVFlare 2.9.0 (a relay job inside the NVFlare server), vantage6 4.15.2 (a vantage6 user creating tasks) and Flower 1.39.0 (a relay ServerApp in a long-lived run), and a mock adapter without a framework.
- `flhub_core` 0.1: PneumoniaMNIST with pinned partitions, a small CNN, deterministic training and a selfcheck.
- Hospital-side clients for the three frameworks, built on each framework's standard client pattern.
- The adapter conformance suite (7 tests) and unit tests.
- Install, start, stop and status scripts for a four-machine testbed.

[0.2.1]: https://github.com/Zare2001/AgnosticFL/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/Zare2001/AgnosticFL/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Zare2001/AgnosticFL/releases/tag/v0.1.0

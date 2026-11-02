# Changelog

All notable changes to this project. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow [Semantic Versioning](https://semver.org/). The protocol has its own version (`flhub/0.1`, `flhub/0.2`, see [docs/protocol.md](docs/protocol.md)).

## [0.3.0] - 2026-11-02

NVFlare's hello-pt example on every framework, and a self-contained kit per hospital.

### Added

- `flhub_core`: dataset `cifar10` (the official python tarball, sha256-pinned; data variants `cifar10-iid` and `cifar10-dir0.3` over the six sites; CSV rows of 3072 pixels, channel-first) and model `hello-pt` (NVFlare's hello-pt `SimpleNetwork`, same parameter names and initial weights). Site files, DataFrames and `from_arrays` take 1x28x28 or 3x32x32 images by their width; `build_model` uses each model's own input channels; `input_shape(model)`.
- `configs/hello-pt.yaml`: hello-pt's settings (batch 32, SGD lr 0.01, momentum 0.9) on six sites across NVFlare, vantage6 and Flower, with `strict_code_version`.
- `deploy/central/hospital_kits.sh` and `deploy/hospital-kit/`: one archive per hospital with its credentials and install/start/stop/status/prepare_data scripts for the framework's standard client.
- `deploy/central/rollout.sh`: ships the checkout's `flhub_core` to all three frameworks (NVFlare job, vantage6 image, Flower FAB).
- `python -m flhub_core.data manifests --variants ...`; `$FLHUB_<DATASET>_URL` names a mirror for a dataset download.
- Run configurations are checked for a model whose input shape does not fit the dataset, and for a dataset other than PneumoniaMNIST without a partition; a missing partition manifest names the command that writes it.
- [docs/hello-pt.md](docs/hello-pt.md) and [docs/guide.md](docs/guide.md): deploying the testbed (firewall, credentials, pitfalls), developing and rolling out a model, dataset or training method, hospital kits, troubleshooting.
- A dataset without a public copy (`url` empty: hospital-owned data) runs without a partition manifest; its run configuration must set `eval: {every: 0}`.
- Plugins: own models and datasets as Python files in `flhub_core/plugins/` (ignored by git), registered with `register_model` / `register_dataset`, shipped with `flhub_core` by all three frameworks and part of its code version; `deploy/central/plugins.sh DIR` installs them, `rollout.sh --plugins DIR` installs and ships them; the vantage6 image tag includes a hash of the plugins. Users train their own model with hospitals without changing the repository ([guide](docs/guide.md) 6.1).

### Unchanged

The MedMNIST data, models and training are bit-identical (site CSVs, fingerprints, `selfcheck` digest); `--variants all` and `manifests` without `--variants` cover the stage-2 variants only.

## [0.2.2] - 2026-10-15

Clean-up fixes for the example scripts. The hub, the adapters, `flhub_core` and the clients are unchanged.

### Fixed

`examples/new-framework/check.sh` and the three `run_local.py` scripts now stop everything they started and remove their temporary files when they end, also when they are interrupted with Ctrl-C, `timeout`, `kill` (SIGTERM) or a closed terminal (SIGHUP); a SIGKILL cannot be caught, so nothing can clean up after it.

- `examples/new-framework/check.sh`: the restart command it writes for the conformance suite's restart test recorded the pid of a subshell instead of the restarted adapter's, so every run left the two restarted adapters running. Its cleanup now also stops a test or a restart in progress and is not cut short by a repeated signal, and Ctrl-C ends the whole script instead of only the running step.
- `examples/nvflare/server/run_local.py`: after a failure, Ctrl-C, `timeout`, SIGTERM or SIGHUP, the processes of NVFlare's simulator no longer outlive the script and its working directory is removed: it ends the relay job, then stops the remaining process tree, and a repeated signal (`timeout` signals the process and then its process group) no longer cuts that clean-up short.
- `examples/flower/run_local.py` stops the processes it started and removes its working directory also when it is ended with SIGTERM or SIGHUP (for example by `timeout` or a closed terminal), and a repeated signal (a second Ctrl-C, or `timeout` signalling the process and then its process group) no longer cuts that clean-up short; before, the processes kept running.
- `examples/vantage6/client/run_local.py` removes its temporary directory (dataset, site CSV, run token) also when it is ended with SIGTERM or SIGHUP; before, the directory stayed behind.

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

[0.2.2]: https://github.com/Zare2001/AgnosticFL/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/Zare2001/AgnosticFL/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/Zare2001/AgnosticFL/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Zare2001/AgnosticFL/releases/tag/v0.1.0

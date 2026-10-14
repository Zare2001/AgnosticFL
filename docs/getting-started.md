# Getting started

This page runs the whole hub on one machine, without any federated-learning framework: the hub core, the **mock adapter** (it trains six example sites in-process with the same `flhub_core` code that hospitals run), the orchestrator, the framework-free simulation and the test suites. It takes about five minutes. Afterwards, [connect a real framework](#next-a-real-framework).

## Requirements

- Linux on x86_64 with `git`, `curl` and [uv](https://docs.astral.sh/uv/). The project was tested on Ubuntu 24.04 (AMD EPYC-Rome, AVX2); other platforms are untested.
- About 1.1 GB of disk for the virtual environment (mostly torch) and 20 MB for data.
- Internet access to PyPI, `download.pytorch.org` (CPU wheels of torch) and `zenodo.org` (the 4 MB PneumoniaMNIST file, downloaded and checked against a pinned sha256 on first use).
- Ports 8800 (hub core) and 8819 (mock adapter) free on 127.0.0.1; `configs/mock-3r.yaml` uses them. On a machine that already runs a hub, copy that config and change both URLs.

Run all commands below in **one shell session**: later steps use the exported variable and the background jobs of earlier ones.

## Install

```bash
git clone https://github.com/Zare2001/AgnosticFL.git
cd AgnosticFL
uv venv --python 3.11.17 .venv
uv pip install --python .venv/bin/python "torch==2.14.1+cpu" --index-url https://download.pytorch.org/whl/cpu
uv pip install --python .venv/bin/python numpy==2.4.4 safetensors==0.8.0 pyyaml==6.0.3 pytest -e ./flhub_core -e .
. .venv/bin/activate
```

These are the versions used in all tests (`deploy/versions.env`). The hub packages (`hub`, `adapters`) have no third-party dependencies of their own; `flhub_core` needs torch, numpy and safetensors, and the orchestrator reads YAML configurations.

Check that training is deterministic on this machine:

```bash
python -m flhub_core.selfcheck
```

It trains each model of the model zoo for one step on synthetic data and prints one hash per model and a final `digest` line. On the reference hardware the digest is `sha256:ac074a0b79a2d3c6782765480164c0465ac598359dfe91695e893e208f4e5034`. Every machine that takes part in one federation must print the same digest; see [Determinism](architecture.md#determinism-and-flhub_core) if yours differs.

## Start the hub core and a mock adapter

```bash
mkdir -p /tmp/agnosticfl
export FLHUB_DATA_DIR=/tmp/agnosticfl/data
python -m hub.core --port 8800 --root /tmp/agnosticfl/hubcore > /tmp/agnosticfl/hubcore.log 2>&1 &
python -m adapters.mock --port 8819 > /tmp/agnosticfl/mock.log 2>&1 &
until curl -sf http://127.0.0.1:8800/health; do sleep 2; done; echo
until curl -sf http://127.0.0.1:8819/health; do sleep 2; done; echo
```

- The **hub core** stores model weights as blobs, addressed by their sha256, and the model's tensor spec.
- The **mock adapter** speaks the same protocol as the NVFlare, vantage6 and Flower adapters, but trains its sites itself. On its first start it downloads the dataset and writes one CSV file per site into `$FLHUB_DATA_DIR`, which can take a minute.

Each health check prints one JSON line with `"ok": true` once the service answers.

## Run a federated job

```bash
python -m hub.orchestrator run --config configs/mock-3r.yaml --runs-dir /tmp/agnosticfl/runs
```

The orchestrator discovers the six sites of the adapter and runs three rounds of weighted FedAvg. Each round stores the global model, dispatches it, collects and validates one envelope per site, aggregates, evaluates on the central test split and writes a checkpoint. At the end it prints a JSON summary with `"status": "finished"`. After three rounds the test accuracy is still 0.625, the share of the majority class; the full 30-round configuration reaches 0.865 ([stage-1 results](results/stage1.md)).

The run directory is `/tmp/agnosticfl/runs/mock-3r-<UTC time>`, with the symlink `mock-3r-latest`:

| File | Content |
|---|---|
| `config.json` | the resolved run configuration and the environment |
| `metrics.jsonl` | one line per round: test metrics, round time, per-site metrics |
| `events.jsonl` | retries, failures, dropped sites, resumes |
| `rounds/rNNNN.safetensors`, `rNNNN.state.safetensors`, `rNNNN.json` | global weights, aggregator state and the round record (with the sha256 of both) after round N |
| `summary.json` | final metrics and the hash of the final model |

## Run the framework-free simulation and check equivalence

```bash
python -m hub.sim --config configs/mock-3r.yaml --runs-dir /tmp/agnosticfl/runs --compare /tmp/agnosticfl/runs/mock-3r-latest
```

`hub.sim` runs the same configuration in one process: the same `flhub_core.train`, the same aggregator, the same participants. With `--compare` it checks the real run against the simulation round by round (the equivalence check, INV-1) and prints:

```
INV-1 full sim vs /tmp/agnosticfl/runs/mock-3r-latest: hashes_equal=True max_abs_diff=0.000e+00 -> PASS
```

`hashes_equal=True` means that the global weights after every round are byte-identical. Every real run of the two test stages passed this check, across NVFlare, vantage6 and Flower ([results](results/README.md)).

## Run the tests

```bash
python -m pytest
python -m pytest tests/conformance --adapter mock
```

- The first command runs the unit tests: `80 passed, 15 skipped`. The skipped tests are the conformance suite (it needs `--adapter`) and two framework tests that need the NVFlare or Flower packages.
- The second runs the **adapter conformance suite** against an in-process hub core and mock adapter on free ports: `12 passed, 1 skipped`. The skipped data-variant test also needs `--variant organamnist-dir0.3`, which downloads the 38 MB OrganAMNIST file. A new adapter must pass the same suite; see [Adding a framework](adding-a-framework.md).

## Stop and clean up

```bash
kill %1 %2
rm -rf /tmp/agnosticfl
```

## Next: a real framework

- **Use an existing federation.** Pick the guide for your framework: [NVFlare](frameworks/nvflare.md), [vantage6](frameworks/vantage6.md) or [Flower](frameworks/flower.md). Each guide covers the server-side adapter, the code a hospital runs, and deployment next to an existing framework server; [Deployment](deployment.md) covers the shared parts.
- **Understand the system.** [Architecture](architecture.md) explains one round step by step, [Protocol](protocol.md) is the contract between the orchestrator and the adapters, and [Client contract](client-contract.md) is what every hospital-side client must do.
- **Configure runs.** Stage-1 configurations (`configs/final.yaml`, `configs/mock-3r.yaml`) list their adapters inline. Stage-2 configurations (`configs/stage2/`) add data partitions, models, aggregators and participation policies, and map sites to adapters through `configs/stage2/topology.yaml`; see [Reproducing results](reproducing-results.md) and [Aggregators](aggregators.md).
- **Add a framework.** See [Adding a framework](adding-a-framework.md).

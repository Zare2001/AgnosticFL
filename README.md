# AgnosticFL

**One federated-learning server for hospitals that use different frameworks.** Hospitals keep their own framework (**NVFlare**, **vantage6** or **Flower**) and run its standard client; one hub trains a single model across all of them with weighted FedAvg or five other server aggregators. Per framework, a small server-side **adapter** makes the hub a normal participant of that framework and speaks one framework-free protocol (`flhub/0.2`, JSON-RPC) to the hub's orchestrator. It is for operators and researchers who want to federate across framework boundaries, or keep aggregation, participation and checkpoints in one place, without asking hospitals to change their software.

## Key results

Measured on a reference testbed of four VMs, two simulated hospitals per framework ([results](docs/results/README.md)):

- **Exact.** Every compared real run equals its framework-free reference with weight difference 0.0: the stage-1 mixed run in all 31 checkpoints, and all 69 stage-2 real runs (66 simulated round by round, the 2 `resnet18-gn` runs by replaying rounds 1-3 and the last, the resumed run against the uninterrupted run).
- **On par with centralized training.** 86.5 % accuracy / 0.925 AUC federated across NVFlare, vantage6 and Flower, against 86.1 % / 0.928 centralized on the pooled data (PneumoniaMNIST, 30 rounds).
- **No hospital code changes.** 0 changed lines in the hospitals' client code while aggregators, data, models and participation changed on the hub; 0 hospital steps per library update.
- **Small overhead.** −20 % to +10 % round time against each framework's own FedAvg, at most +0.73 s per round.
- **Large models.** Updates of 512 MiB per site carried by NVFlare and Flower; vantage6 up to 128 MiB.
- **Recovers by itself.** Hospital crashes, stragglers, a hub crash (resumed bit-exactly) and framework-server restarts all recovered without manual action.

## Architecture

```
 operator's machine (<central-host>)
 ┌─────────────────────────────────────────────────────────────────────────────┐
 │ ORCHESTRATOR  hub/orchestrator   rounds · aggregation · validation · eval   │
 │ HUB CORE      hub/core :8800     blob store (sha256-addressed) · model spec │
 └───────▲─────────────────────▲─────────────────────▲─────────────────────────┘
   flhub/0.2 JSON-RPC: discover · dispatch · tasks/get · collect · cancel
 ┌───────┴────────┐    ┌───────┴────────┐    ┌───────┴────────┐
 │ NVFlare adapter│    │vantage6 adapter│    │ Flower adapter │
 │ :8811 + relay  │    │ :8812 (v6 user)│    │ :8813 + relay  │
 │ ModelController│    │                │    │ ServerApp      │
 ├────────────────┤    ├────────────────┤    ├────────────────┤
 │ NVFlare server │    │ vantage6 server│    │ Flower         │
 │ :8002 (mTLS)   │    │ :7601          │    │ SuperLink :9092│
 └───────▲────────┘    └───────▲────────┘    └───────▲────────┘
 hospitals: NVFlare clients    vantage6 nodes          Flower SuperNodes
            (Client API)       (algorithm image)       (ClientApp)
```

Every hospital trains with the same shared library, `flhub_core` (data loading, models, a deterministic training loop). That makes aggregation across frameworks valid, and it makes training bit-reproducible: the same weights, data and configuration give the same result on every machine and inside the vantage6 image.

**One round:** the orchestrator stores the global model in the hub core → `dispatch` to every adapter in parallel → each adapter sends the model through its framework's own channel to its hospitals → each hospital runs `flhub_core.train` in its standard client → the adapter turns each reply into an envelope (weights stored in the hub core) → the orchestrator validates every envelope, aggregates (float64, fixed site order), evaluates on its test split and checkpoints. Details: [architecture](docs/architecture.md), [protocol](docs/protocol.md).

## What it supports

| | NVFlare | vantage6 | Flower |
|---|---|---|---|
| Tested version | 2.9.0 | 4.15.2 | 1.39.0 |
| Adapter | relay job inside the NVFlare server (`RelayAdapter`) | vantage6 user creating tasks (`BaseAdapter`) | relay ServerApp in a long-lived run (`RelayAdapter`) |
| Hospital code (example) | [Client API script](clients/nvflare/client.py) | [algorithm package](clients/vantage6/v6_flhub/__init__.py) | [ClientApp](clients/flower/flhub_flower/client_app.py) |
| New training code reaches hospitals as | job custom code | algorithm image tag | FAB |
| Largest update tested | 512 MiB | 128 MiB | 512 MiB |

Hub features for all frameworks: FedAvg, FedAvgM, FedAdam, FedYogi, FedAdagrad, FedProx; partial participation with `min_sites` and deadlines; per-site cancel; bit-exact resume after a crash; data variants; mixed federations of all three frameworks; up to 12 sites tested. Not supported: secure aggregation, differential privacy, GPUs, robust aggregators. Full list: [support matrix](docs/support-matrix.md), [limitations and future work](docs/limitations-and-future-work.md).

## Quick start (one machine, no framework)

The hub core, a mock adapter that trains six example sites, the orchestrator and the framework-free simulation; Linux with [uv](https://docs.astral.sh/uv/):

```bash
git clone https://github.com/Zare2001/AgnosticFL.git
cd AgnosticFL
uv venv --python 3.11.17 .venv
uv pip install --python .venv/bin/python "torch==2.14.1+cpu" --index-url https://download.pytorch.org/whl/cpu
uv pip install --python .venv/bin/python numpy==2.4.4 safetensors==0.8.0 pyyaml==6.0.3 pytest -e ./flhub_core -e .
. .venv/bin/activate
mkdir -p /tmp/agnosticfl
export FLHUB_DATA_DIR=/tmp/agnosticfl/data
python -m hub.core --port 8800 --root /tmp/agnosticfl/hubcore > /tmp/agnosticfl/hubcore.log 2>&1 &
python -m adapters.mock --port 8819 > /tmp/agnosticfl/mock.log 2>&1 &
until curl -sf http://127.0.0.1:8800/health; do sleep 2; done; echo
until curl -sf http://127.0.0.1:8819/health; do sleep 2; done; echo
python -m hub.orchestrator run --config configs/mock-3r.yaml --runs-dir /tmp/agnosticfl/runs
python -m hub.sim --config configs/mock-3r.yaml --runs-dir /tmp/agnosticfl/runs --compare /tmp/agnosticfl/runs/mock-3r-latest
```

The last command prints `INV-1 … hashes_equal=True max_abs_diff=0.000e+00 -> PASS`: the run through the adapter equals the simulation. The step-by-step version with tests and clean-up is [getting started](docs/getting-started.md); to connect a real framework, see its guide: [NVFlare](docs/frameworks/nvflare.md), [vantage6](docs/frameworks/vantage6.md), [Flower](docs/frameworks/flower.md).

## Cost and efficiency at a glance

| | NVFlare | vantage6 | Flower |
|---|---|---|---|
| Adapter code (code lines) | 604 | 290 | 553 |
| Hospital steps per library update | 0 | 0 | 0 |
| Hub vs native FedAvg, per round (small CNN / 45 MiB) | −6.5 % / +4.1 % | 0 % / −14 % | −20 % / +9.8 % |
| Fixed cost per round + cost per MiB | 2.23 s + 0.021 s/MiB | 4.13 s + 1.49 s/MiB up to 64 MiB, superlinear above | 5.48 s + 0.061 s/MiB |
| Bytes on the wire ÷ update size | 1.005–1.064 | 4.97–9.08 | 1.002–1.005 |

![Round time of each framework's own FedAvg against the hub](docs/results/figures/e7_native_vs_hub.png)

Implementation cost, hospital-side cost, memory, network, scaling and robustness, with sources: [cost and efficiency](docs/cost-and-efficiency.md).

## Documentation

| Page | |
|---|---|
| [Getting started](docs/getting-started.md) | local quick start, tests, next steps |
| [Architecture](docs/architecture.md) | components, one round, determinism, trust and security model |
| [Protocol](docs/protocol.md) | `flhub/0.1` and `flhub/0.2` |
| [Client contract](docs/client-contract.md) | what every hospital-side client must do |
| [Examples](examples/README.md) | per framework a client and a server example to copy; a skeleton for a new framework |
| Framework guides: [NVFlare](docs/frameworks/nvflare.md), [vantage6](docs/frameworks/vantage6.md), [Flower](docs/frameworks/flower.md) | adapter, hospital code, deployment, cost, limits, troubleshooting |
| [Adding a framework](docs/adding-a-framework.md) | write an adapter, pass the conformance suite |
| [Aggregators](docs/aggregators.md) | the six server aggregators and how to add one |
| [Deployment](docs/deployment.md) | next to an existing framework server; the reference testbed |
| [Reproducing results](docs/reproducing-results.md) | configs and scripts behind every result |
| [Support matrix](docs/support-matrix.md), [Limitations and future work](docs/limitations-and-future-work.md) | what works, what does not |
| [Cost and efficiency](docs/cost-and-efficiency.md), [Results](docs/results/README.md) | measurements of both test stages |
| [Design decisions](docs/design/decisions.md) | the architecture decision records |

All pages: [docs/README.md](docs/README.md).

## Repository layout

| Path | What |
|---|---|
| `hub/core/` | hub core: blob store and model spec over HTTP |
| `hub/orchestrator/` | rounds, participation, validation, checkpoints, resume, run CLI |
| `hub/aggregators/` | the six server aggregators |
| `hub/sim/` | framework-free reference runs: FedAvg simulation, centralized and local baselines, replay |
| `adapters/common/` | the protocol, JSON-RPC server, `BaseAdapter`, `RelayAdapter`, hub-core client |
| `adapters/{nvflare,vantage6,flower}/` | one adapter per framework; `adapters/mock/` trains without a framework |
| `flhub_core/` | the shared training library that every hospital runs |
| `clients/{nvflare,vantage6,flower}/` | the hospital-side code per framework (`clients/flower` also holds the relay ServerApp, which runs only next to the SuperLink) |
| `tests/` | unit tests, the adapter conformance suite, Flower relay tests |
| `examples/` | per framework a minimal client and an annotated server side; a skeleton for a new framework ([README](examples/README.md)) |
| `configs/` | run configurations of both test stages ([README](configs/README.md)) |
| `deploy/` | install, start, stop and status scripts of the reference testbed ([README](deploy/README.md)) |
| `scripts/` | equivalence check, monitoring, the stage-2 campaign tooling, docs tooling ([README](scripts/README.md)) |
| `tools/` | the testbed's message bus, used by `deploy/` and `scripts/` ([README](tools/README.md)) |
| `docs/` | documentation, results, evidence and figures |

## Project status

Research prototype, version 0.2.1 ([changelog](CHANGELOG.md)). Tested with NVFlare 2.9.0, vantage6 4.15.2 and Flower 1.39.0 on Python 3.11.17 with torch 2.14.1 (CPU) on Ubuntu 24.04 ([`deploy/versions.env`](deploy/versions.env)). The hub sees every model update in plaintext and has no secure aggregation or differential privacy; see the [trust and security model](docs/architecture.md#trust-and-security-model) before you use it with real patient data. Contributions: [CONTRIBUTING.md](CONTRIBUTING.md).

## Citation

If you use this software, please cite it with the metadata in [`CITATION.cff`](CITATION.cff) (GitHub shows it under "Cite this repository").

## License

No license has been chosen yet. Until a LICENSE file is added, all rights are reserved by the author.

## Acknowledgements

Developed at [SURF](https://www.surf.nl), the collaborative organisation for IT in Dutch education and research; the test infrastructure ran on SURF Research Cloud. The datasets are from [MedMNIST v2](https://medmnist.com) (CC BY 4.0).

## History

The complete development history, including the material of the automated build, is kept on branch [`archive/agentic-build`](https://github.com/Zare2001/AgnosticFL/tree/archive/agentic-build) and at the tags [`stage1-final`](https://github.com/Zare2001/AgnosticFL/tree/stage1-final) and [`stage2-final`](https://github.com/Zare2001/AgnosticFL/tree/stage2-final).

# AgnosticFL

Framework-agnostic federated learning hub. Hospitals keep their own federated learning framework (**NVFlare**, **vantage6** or **Flower**) and run its standard client, unchanged; one central hub runs plain, weighted **FedAvg** across all of them. Per framework, a server-side **adapter** makes the hub look like a normal part of that framework, and speaks one small protocol (`flhub/0.1`, JSON-RPC) to a framework-free orchestrator.

**Result**: in a 30-round run with 6 hospitals (2 NVFlare, 2 vantage6, 2 Flower), the federated model reaches 86.5 % test accuracy / 0.925 AUC on PneumoniaMNIST, on par with centralized training on the pooled data (86.1 % / 0.928). It is bit-identical (max weight difference 0.0 in every round) to a framework-free FedAvg simulation. Results and evidence: [`docs/results/stage1.md`](docs/results/stage1.md). Protocol: [`docs/protocol.md`](docs/protocol.md). Decisions: [`docs/design/decisions.md`](docs/design/decisions.md).

**Stage 2** (full system and performance test: non-IID data, six server aggregators, models up to 11 M parameters, payloads up to 512 MB, faults and restarts, native-framework baselines; every real run equal to the framework-free simulation): [`docs/results/stage2.md`](docs/results/stage2.md).

## Architecture

```
 central VM (145.38.205.58)
 ┌─────────────────────────────────────────────────────────────────────────────┐
 │ ORCHESTRATOR  hub/orchestrator   rounds · FedAvg · global eval · W&B        │
 │ HUB CORE      hub/core :8800     blob store (sha256-addressed) · model spec │
 └───────▲─────────────────────▲─────────────────────▲─────────────────────────┘
   flhub/0.1 JSON-RPC: discover · dispatch · tasks/get · collect · cancel
 ┌───────┴────────┐    ┌───────┴────────┐    ┌───────┴────────┐
 │ NVFlare adapter│    │vantage6 adapter│    │ Flower adapter │
 │ :8811 + relay  │    │ :8812 (v6 user)│    │ :8813 + relay  │
 │ ModelController│    │                │    │ ServerApp      │
 ├────────────────┤    ├────────────────┤    ├────────────────┤
 │ NVFlare server │    │ vantage6 server│    │ Flower         │
 │ :8002 (mTLS)   │    │ :7601, reg 5050│    │ SuperLink :9092│
 └───────▲────────┘    └───────▲────────┘    └───────▲────────┘
 hospitals: NVFlare clients    vantage6 nodes          Flower SuperNodes
            nvf-h1, nvf-h2     v6-h3, v6-h4            flw-h5, flw-h6
            (Client API)       (algorithm image)       (ClientApp)
```

Every hospital trains with the same shared library `flhub_core` (model, data loading, deterministic training loop). That is what makes FedAvg across frameworks valid, and it makes training bit-reproducible: the same weights, data and config give the same result on every VM and inside the vantage6 image (`python -m flhub_core.selfcheck`).

One round: the orchestrator stores the global model in the hub core → `dispatch` to every adapter in parallel → each adapter sends the model through its framework's own channel to its hospitals → the hospitals run `flhub_core.train` in their standard client → the adapter turns the replies into envelopes (weights stored as hub-core blobs) → the orchestrator validates every envelope, runs FedAvg (float64, site order fixed), evaluates on the central test split, logs and checkpoints.

## Repository layout

| Path | What |
|---|---|
| `flhub_core/` | shared library: PneumoniaMNIST data + partitions, CNN, deterministic `train`/`evaluate`, safetensors weights io |
| `hub/core/` | hub core HTTP service (blobs, model spec) |
| `hub/orchestrator/` | round loop, FedAvg, validation, W&B logging, run CLI |
| `hub/sim/` | framework-free FedAvg reference and centralized baseline (same round loop) |
| `adapters/common/` | protocol helpers, JSON-RPC server, `BaseAdapter`, `RelayAdapter`/`RelayClient`, hub-core client |
| `adapters/{nvflare,vantage6,flower,mock}/` | one adapter per framework (+ a framework-free mock) |
| `clients/{nvflare,vantage6,flower}/` | the standard hospital-side code per framework |
| `deploy/` | install/start/stop/status scripts per VM, `versions.env`, `topology.env` |
| `configs/` | run configs (`final.yaml` = the final runs) |
| `tests/` | unit tests and the adapter conformance suite |
| `scripts/` | equivalence check, final-runs driver, W&B cleanup |

## Quick start (one machine, no framework)

```bash
uv venv --python 3.11.17 ~/venvs/hub
uv pip install --python ~/venvs/hub/bin/python "torch==2.14.1+cpu" --index-url https://download.pytorch.org/whl/cpu
uv pip install --python ~/venvs/hub/bin/python numpy==2.4.4 safetensors==0.8.0 pyyaml wandb pandas pytest \
    -e ./flhub_core -e .
. ~/venvs/hub/bin/activate
python -m hub.core --port 8800 --root /tmp/hubcore &           # blob store
python -m adapters.mock --port 8819 &                           # adapter without a framework
python -m hub.orchestrator run --config configs/mock-3r.yaml --wandb disabled
python -m hub.sim --config configs/mock-3r.yaml --wandb disabled
python scripts/equivalence_check.py ~/flhub-runtime/runs/mock-3r-latest ~/flhub-runtime/runs/sim-fedavg-reference-latest
pytest                                                          # unit tests
pytest tests/conformance --adapter http://127.0.0.1:8819 -v -s # conformance suite against the mock
```

The dataset (4 MB) is downloaded from Zenodo on first use and checked against a pinned sha256.

## Full deployment (4 VMs)

Ports and IPs: `deploy/topology.env`; versions: `deploy/versions.env`. In order:

1. **central**: `bash deploy/central/install.sh && bash deploy/central/start.sh` (hub core as systemd unit, Docker registry :5050), then per framework `bash deploy/central/<fw>/install.sh && bash deploy/central/<fw>/start.sh` and send the hospital credentials with `tools/bus put --secret` (NVFlare startup kits, vantage6 node API keys).
2. **framework VMs**: `bash deploy/<fw>-vm/install.sh && bash deploy/<fw>-vm/start.sh` with the received credentials.
3. **check**: `bash deploy/central/status.sh` (exit 0 = everything healthy and all six sites online).
4. **run**: `bash scripts/run_final.sh all` (six runs logged to W&B with tag `final`, then the equivalence check).

## Adding a new framework adapter

1. Subclass `adapters.common.BaseAdapter` (or `RelayAdapter` if the framework's server workflow must run inside the framework server) and implement `site_status()` and `run_round(task, weights, client_config)`; see `adapters/mock/service.py` (60 lines) for the smallest complete example.
2. On the hospital side, use the framework's normal client pattern and call `flhub_core.train.train(weights, flhub_core.data.load_site(path), client_config)`; reply with the weights, `num_examples`, the metrics and `flhub_core.code_version()`.
3. Pass the admission test: `pytest tests/conformance --adapter http://127.0.0.1:<port> -v -s`.
4. Add `deploy/central/<fw>/{install,start,stop,status}.sh` and `deploy/<fw>-vm/...`, and an entry in `configs/final.yaml`. Nothing in the orchestrator or hub core changes.

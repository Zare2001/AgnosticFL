# NVFlare's hello-pt on every framework

NVIDIA's [hello-pt](https://github.com/NVIDIA/NVFlare/tree/main/examples/hello-world/hello-pt) trains a small CNN on CIFAR-10 with FedAvg, on NVFlare only. On the hub, the same model and data train in one federation whose hospitals run NVFlare, vantage6 or Flower, each its own standard client. The operator hands every hospital a **kit** (its credentials plus install/start scripts); after that, changing the model, the data variant or the training settings is done centrally and reaches the hospitals through each framework's own code channel.

The general procedure (deploying, developing your own algorithm, rolling it out) is in the [guide](guide.md).

## What maps where

| hello-pt | On the hub |
|---|---|
| `model.py`: `SimpleNetwork` | `flhub_core` model `hello-pt` ([`model.py`](../flhub_core/src/flhub_core/model.py)): same layers, creation order and parameter names, so state dicts carry over; with the run seed 202610 the initial weights equal hello-pt's `create_model()` |
| `prepare_data.py`: torchvision CIFAR-10, every client the full set | dataset `cifar10` ([`data.py`](../flhub_core/src/flhub_core/data.py)): the official python tarball, sha256-pinned; split over the six sites as data variant `cifar10-iid` (stratified, 1/6 each) or `cifar10-dir0.3` (label skew) |
| `Normalize((0.5,)*3, (0.5,)*3)` in the data loader | the same two operations at the start of the model's `forward()` (`flhub_core` feeds pixel / 255) |
| `client.py`: SGD lr 0.01, momentum 0.9, batch 32, cross-entropy, 1 local epoch | `train_config` of [`configs/hello-pt.yaml`](../configs/hello-pt.yaml), run by `flhub_core.train` at every hospital |
| `job.py`: `FedAvgRecipe(min_clients=2, num_rounds=3)` | `configs/hello-pt.yaml`: six sites on three frameworks, 20 rounds, FedAvg; `--rounds N` overrides |
| client-side evaluation on the CIFAR-10 test set | the hub evaluates the global model on the CIFAR-10 test set after every round (accuracy, balanced accuracy, AUC) |

Differences that remain: `flhub_core.train` starts each round with a fresh optimizer (hello-pt's resident client keeps its momentum buffer across rounds) and uses one CPU thread with deterministic algorithms, so that every framework computes the same update and the run can be checked hash by hash against the framework-free simulation ([architecture](architecture.md#determinism-and-flhub_core)).

## Try it without a deployment

In the virtual environment of [getting started](getting-started.md), from the repository root (downloads CIFAR-10 once, 170 MB):

```bash
export FLHUB_DATA_DIR=/tmp/hello-pt-data
python -m flhub_core.data manifests --variants cifar10-iid --out configs/stage2/partitions
python -m hub.sim --config configs/hello-pt.yaml --runs-dir /tmp/hello-pt-runs
```

## On the testbed

Operator, on the central machine, after applying the change (`git am` or `git pull`):

```bash
cd ~/AgnosticFL
# 1. hub data: CIFAR-10 test set for evaluation, partition manifests that pin each site's file
~/venvs/hub/bin/python -m flhub_core.data manifests --variants cifar10-iid cifar10-dir0.3 --out configs/stage2/partitions
# 2. ship the new flhub_core to all three frameworks (NVFlare job, vantage6 image, Flower FAB)
bash deploy/central/rollout.sh
# 3. one kit per hospital, in ~/flhub-runtime/hospital-kits/<site>.tgz
bash deploy/central/hospital_kits.sh
```

Each hospital, with its kit (sent over a secure channel, e.g. `scp -3` from the operator's laptop):

```bash
tar -xzf nvf-h1.tgz && cd flhub-nvf-h1
./install.sh        # the framework's standard client, pinned versions, credentials in place
./prepare_data.sh   # demo: this site's share of CIFAR-10, checked against the operator's manifest
./start.sh          # connects to the operator's server
./status.sh
```

Then the run, on the central machine (all six sites online in `bash deploy/central/status.sh`):

```bash
. ~/venvs/hub/bin/activate
python -m hub.orchestrator run --config configs/hello-pt.yaml --rounds 3 --name hello-pt-3r   # smoke test
python -m hub.orchestrator run --config configs/hello-pt.yaml
python -m hub.sim --config configs/hello-pt.yaml --compare ~/flhub-runtime/runs/hello-pt-latest
```

On the reference testbed, where `deploy/<framework>-vm/` already runs the six sites, stop those first (`bash deploy/<framework>-vm/stop.sh` on each hospital machine; it also disables their watchdog), because a kit connects with the same site id.

## Hospital kits

`deploy/central/hospital_kits.sh [--variants "cifar10-iid ..."] [site ...]` writes one archive per site (default: the six base sites; extra sites need no step on central before the kit). No kit holds a hospital private key or node key:

| In the kit | NVFlare | vantage6 | Flower |
|---|---|---|---|
| credentials | none: `./install.sh` makes the key and `nvflare/<site>.request.zip`; with the operator's `<site>.signed.zip` a second `./install.sh` builds the startup kit `nvflare/<site>/` (root CA fingerprint from `kit.env`) | `credentials/api_key`: made by the kit's `./install.sh` on the hospital's machine; the hospital sends it to the operator, who registers it with `vantage6/register_node.sh` | none (`--insecure` on the testbed) |
| installed by `install.sh` | NVFlare client, torch (CPU), numpy, safetensors in `venv/`; the server name in `/etc/hosts` | vantage6 CLI, the node image, the operator's registry in Docker's `insecure-registries` | Flower, torch (CPU), numpy, safetensors in `venv/` |
| started by `start.sh` | the kit's `startup/start.sh` with `FLHUB_DATA_PATH` | `v6 node start`, one CSV database per data file | `flower-supernode` under a restart loop, node config `site-id`, `data-path` |
| training code | arrives with the job (`custom/`) | inside the algorithm image | inside the FAB |

Every kit also holds `kit.env` (site, framework, operator address, pinned versions), `prepare_data.sh` with a copy of `flhub_core` used only as a data tool (numpy), the partition manifests, and a README for the hospital. Kits contain credentials: mode 600, hand them over a secure channel.

## Changing the model or the data

| Change | Operator | Hospitals |
|---|---|---|
| another zoo model, data variant, aggregator, rounds or training setting | edit the run configuration | nothing (a new data variant needs its file, see below) |
| a new model or training code | add it to `flhub_core` (a class in `_ZOO` of `model.py`, with `input_shape`), commit, `bash deploy/central/rollout.sh` | nothing: NVFlare gets it with the next job, vantage6 pulls the new image tag, Flower gets the new FAB |
| a new data variant | `flhub_core.data`: a `DatasetInfo` (image shape, classes, partitions), its manifest | put `<variant>-<site>.csv` into `~/flhub-data` (demo: `./prepare_data.sh <variant>`); vantage6: `./stop.sh && ./start.sh` |

The run configuration checks that the model's input shape matches the dataset (`hello-pt` takes 3x32x32, the MedMNIST models 1x28x28), and with `strict_code_version: true` the hub accepts only updates from sites that ran the same `flhub_core` as the hub.

## Tested

2026-11-02, on one machine (Python 3.11, torch 2.14.1, NVFlare 2.9.0, Flower 1.39.0, vantage6-algorithm-tools 4.15.2), with a synthetic CIFAR-10 tarball of the official layout (50 000 + 10 000 images; the test machine could not reach the CIFAR-10 host):

| What | Result |
|---|---|
| `hello-pt` model against NVFlare's `model.py` | same parameter names; initial weights for seed 202610 and forward pass bit-identical |
| `hub.sim --config configs/hello-pt.yaml --rounds 3` | finished; 6 sites, 7 s per round with 4 workers |
| NVFlare kits `nvf-h1`, `nvf-h2` against a provisioned NVFlare server on the same machine (central `provision.sh`, relay job, adapter) | `install.sh`, `prepare_data.sh` (sha256 against the manifest), `start.sh` registered, `status.sh` HEALTHY; 2 rounds; `INV-1 hashes_equal=True`; no `flhub_core` in the hospital venv |
| Flower kits `flw-h5`, `flw-h6` against a local SuperLink and the library's Flower adapter | same steps; 2 rounds; `INV-1 hashes_equal=True`; the ClientApp ran the FAB's `flhub_core` (none installed at the hospital) |
| vantage6 algorithm on a CIFAR-10 site file with vantage6's `MockAlgorithmClient` | update equal to `flhub_core.train` (max abs diff 0.0) |
| vantage6 kit | node configuration rendered and parsed (databases `default`, `cifar10_iid`; allowed image); the node itself not run (no Docker on the test machine) |
| `deploy/central/rollout.sh` | not run here (needs the central services, systemd and Docker); it calls the central `install.sh`, `start.sh` and `restart.sh` scripts of each framework |
| unit tests, mock conformance suite, `examples/new-framework/check.sh` | pass; MedMNIST site CSVs, fingerprints and the `selfcheck` digest unchanged |

Not measured here: accuracy on the real CIFAR-10 (the synthetic images are trivially separable).

## Limits

- CIFAR-10 site files are CSV, about 80 MB per site at 1/6 of the training set; parsing one takes about 1 s (numpy) to 2 s (pandas, vantage6). A Flower ClientApp, a fresh process per message, parses its file every round.
- CPU only, as the rest of the project; hello-pt's GPU option is not used.
- A kit connects one site. New site names beyond the testbed's (nvf-h1 ... flw-h12) need provisioning, a vantage6 organisation and an entry in `configs/stage2/topology.yaml`.

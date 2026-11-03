# Guide: deploy AgnosticFL and bring your own algorithm

For three readers: the **operator** who runs the hub, the **hospitals** that join it with their own framework, and whoever **develops a model or training method** for the federation. It collects what was learned while setting up the four-VM testbed on SURF Research Cloud and while moving NVFlare's hello-pt example onto all three frameworks. Background: [architecture](architecture.md), [client contract](client-contract.md), [hello-pt](hello-pt.md).

**The one idea.** Every hospital trains with the same library, `flhub_core` (model, data loading, training loop). The framework a hospital runs (NVFlare, vantage6 or Flower) only carries weights to and from the hub. So a new algorithm is a change to `flhub_core`, made once, and it reaches every hospital through each framework's own code channel; the hospitals install nothing of the hub.

## Contents

1. [Concepts](#1-concepts)
2. [Try everything on one machine](#2-try-everything-on-one-machine)
3. [Deploy the testbed](#3-deploy-the-testbed)
4. [Train across the frameworks](#4-train-across-the-frameworks)
5. [Watch the hospitals](#5-watch-the-hospitals)
6. [Develop your own algorithm](#6-develop-your-own-algorithm)
7. [For hospitals: your kit](#7-for-hospitals-your-kit)
8. [Troubleshooting](#8-troubleshooting)
9. [Reference](#9-reference)

## 1. Concepts

| Term | What it is | Runs on |
|---|---|---|
| hub core | stores model weights by sha256 (port 8800, local only) | central |
| orchestrator | runs the rounds: dispatch, collect, check, aggregate (FedAvg and five others), evaluate, checkpoint | central |
| adapter | one per framework; makes the hub a normal user of that framework's server (ports 8811, 8812, 8813, local only) | central |
| framework server | NVFlare server, vantage6 server, Flower SuperLink, unchanged | central |
| hospital client | the framework's standard client: NVFlare client, vantage6 node, Flower SuperNode | hospitals |
| `flhub_core` | the shared training code; the only code that must be identical everywhere | shipped to the hospitals by each framework |
| site | one hospital's id: `nvf-h1`, `nvf-h2` (NVFlare), `v6-h3`, `v6-h4` (vantage6), `flw-h5`, `flw-h6` (Flower) | |
| data variant | `<dataset>-<partition>`, e.g. `cifar10-iid`; a hospital holds it as `<variant>-<site>.csv` | hospitals |
| kit | one archive per hospital: its credentials plus install/start/stop/status scripts | made on central |

## 2. Try everything on one machine

No deployment, no credentials: each example starts what it needs on `127.0.0.1` and cleans up afterwards. Use one virtual environment per framework (their dependencies conflict); on the testbed, one VM per framework works well.

```bash
git clone https://github.com/Zare2001/AgnosticFL.git && cd AgnosticFL
curl -LsSf https://astral.sh/uv/install.sh | sh && source $HOME/.local/bin/env     # if uv is missing
uv venv --python 3.11.17 .venv
uv pip install --python .venv/bin/python "torch==2.14.1+cpu" --index-url https://download.pytorch.org/whl/cpu
uv pip install --python .venv/bin/python numpy==2.4.4 safetensors==0.8.0 pyyaml==6.0.3 pytest -e ./flhub_core -e .
. .venv/bin/activate
python -m flhub_core.selfcheck        # the digest must be the same on every machine of a federation
```

On macOS install `torch==2.14.1` from PyPI instead (the `+cpu` build exists for Linux only); results on a Mac then differ from the Linux machines in the last digits, which is fine for development.

| Example | Extra packages | Command |
|---|---|---|
| NVFlare (its simulator) | `nvflare==2.9.0` | `python examples/nvflare/server/run_local.py` |
| Flower (local SuperLink, two SuperNodes) | `flwr==1.39.0` | `python examples/flower/run_local.py` |
| vantage6 (its `MockAlgorithmClient`) | `pandas==2.3.3 vantage6-algorithm-tools==4.15.2 vantage6-client==4.15.2` | `FLHUB_DATA_DIR=/tmp/v6-example-data python examples/vantage6/client/run_local.py` |
| a new framework (skeleton) | none | `FLHUB_DATA_DIR=/tmp/agnosticfl/data bash examples/new-framework/check.sh` |
| simulation, no framework | none | `python -m hub.sim --config examples/vantage6/server/run-config.yaml --rounds 30 --runs-dir /tmp/v6-example-runs` |

Install the extras with `uv pip install --python .venv/bin/python <packages>`. Each run ends with `hashes_equal=True ... -> PASS`: the model trained through the framework equals the framework-free simulation bit for bit. The examples train a small CNN on PneumoniaMNIST (28x28 chest X-rays, normal or pneumonia), two sites of about 785 images each. After 3 rounds the test accuracy is 0.625 (every image called "pneumonia"); with `--rounds 30` it reaches about 0.86 (the vantage6 simulation above: 0.8638, AUC 0.9246).

## 3. Deploy the testbed

### 3.1 Machines

| Role | IP | Hostname | SURF workspace |
|---|---|---|---|
| **central** (hub, all three framework servers) | 145.38.205.58 | `ubuntuserver` | FrameworkServer |
| NVFlare hospitals `nvf-h1`, `nvf-h2` | 145.38.189.142 | `ubuntunvflare` | FrameworkNVFLARE |
| vantage6 hospitals `v6-h3`, `v6-h4` | 145.38.186.63 | `ubuntuvantage` | FrameworkVantage6 |
| Flower hospitals `flw-h5`, `flw-h6` | 145.38.187.20 | `ubuntuflower` | FrameworkFlower |

The addresses are in [`deploy/topology.env`](../deploy/topology.env). **Run `deploy/central/*` only on central** and `deploy/<framework>-vm/*` only on that framework's hospital machine. Every block below names its machine; check the prompt before you paste.

### 3.2 Prerequisites

On every machine: `curl -LsSf https://astral.sh/uv/install.sh | sh && source $HOME/.local/bin/env`, and a clone of the repository in `~/AgnosticFL`.

Docker on **central** and on the **vantage6** machine (the image registry, the vantage6 server and nodes):

```bash
sudo apt-get update && sudo apt-get install -y docker.io
sudo usermod -aG docker $USER
exit                      # log out and SSH back in: the group applies to new logins only
docker ps                 # must work without sudo
```

Name resolution of the NVFlare server name `flhub-central`:

```bash
echo "127.0.0.1 flhub-central" | sudo tee -a /etc/hosts        # on central
echo "145.38.205.58 flhub-central" | sudo tee -a /etc/hosts    # on the NVFlare machine (the kits do this themselves)
```

### 3.3 Firewall

The hospitals connect **out** to central; central needs these inbound rules, and nothing else beyond SSH. In SURF Research Cloud they are security-group rules of the **central** workspace (FrameworkServer), one rule per port and source address, each address in CIDR form (`/32` for one machine):

| From port | To port | IP address(es) | Traffic | Protocol | For |
|---|---|---|---|---|---|
| 8002 | 8002 | 145.38.189.142/32 | in | tcp | NVFlare hospitals |
| 7601 | 7601 | 145.38.186.63/32 | in | tcp | vantage6 server |
| 5050 | 5050 | 145.38.186.63/32 | in | tcp | vantage6 image registry |
| 9092 | 9092 | 145.38.187.20/32 | in | tcp | Flower hospitals |

Pitfalls seen: a rule added to a hospital's workspace instead of central's has no effect; a range `5050 9092` also opens the NVFlare admin port 8003; the address field takes one address per rule (`145.38.189.142/32`, not a list, not without `/32`). Check from each hospital machine once the central services run:

```bash
for p in 8002 7601 5050 9092; do timeout 3 bash -c "</dev/tcp/145.38.205.58/$p" && echo "$p open" || echo "$p BLOCKED"; done
```

Each machine needs only its own ports open (NVFlare 8002; vantage6 7601 and 5050; Flower 9092). If one is blocked: `sudo ufw status` on central (inactive on the testbed), then `sudo tcpdump -ni any 'tcp port 8002'` on central while you test from the hospital: no packets means the cloud firewall drops them (rule on the wrong workspace, wrong source address, not yet applied); packets without replies mean central's own firewall (`sudo iptables -S INPUT`).

### 3.4 Central

```bash
cd ~/AgnosticFL                                   # on ubuntuserver only
bash deploy/central/install.sh && bash deploy/central/start.sh
bash deploy/central/nvflare/install.sh && bash deploy/central/nvflare/start.sh       # NVFlare root CA, server and admin kits
bash deploy/central/vantage6/install.sh && bash deploy/central/vantage6/start.sh     # server, algorithm image (no node keys)
bash deploy/central/flower/install.sh && bash deploy/central/flower/tls.sh && bash deploy/central/flower/start.sh   # Flower CA, SuperLink certificate; TLS + SuperNode authentication
bash deploy/central/status.sh
```

`status.sh` reports every service; until hospitals connect their sites are offline. The **agent bus** line (port 7700) always shows FAIL unless you run the bus; it is optional (requests, public keys and certificates can also go by `scp -3` or e-mail; a vantage6 node key needs a secure channel) and can be ignored. The extra sites `nvf-h7`, `nvf-h8`, `v6-h9`, `v6-h10`, `flw-h11`, `flw-h12` belong to the 12-site test and stay offline.

Central creates no hospital credential. It prints two public fingerprints that the hospitals check: the NVFlare root CA (`~/flhub-runtime/nvflare/server/rootCA.fingerprint`) and the sha256 of the Flower CA certificate (`~/flhub-runtime/flower/tls/ca.crt`, printed by `tls.sh`). Until each hospital's own credential is approved or registered ([3.5](#35-hospitals)), no hospital connects. `bash deploy/central/nvflare/check_keys.sh` proves that no NVFlare hospital private key is on central.

### 3.5 Hospitals

Two ways; use one per site, never both (they connect with the same site id).

**A. Kits (recommended; also what a real hospital gets).** On central:

```bash
bash deploy/central/hospital_kits.sh            # ~/flhub-runtime/hospital-kits/<site>.tgz, one per site
```

Copy each kit from **your laptop** (no files land on the laptop; `-3` routes the copy through it, using your normal SSH login):

```bash
scp -3 'zpalanciya@145.38.205.58:flhub-runtime/hospital-kits/nvf-h*.tgz' zpalanciya@145.38.189.142:
scp -3 'zpalanciya@145.38.205.58:flhub-runtime/hospital-kits/v6-h*.tgz'  zpalanciya@145.38.186.63:
scp -3 'zpalanciya@145.38.205.58:flhub-runtime/hospital-kits/flw-h*.tgz' zpalanciya@145.38.187.20:
```

On each hospital machine, for each of its kits ([section 7](#7-for-hospitals-your-kit)):

```bash
tar -xzf nvf-h1.tgz && cd flhub-nvf-h1 && ./install.sh   # makes this hospital's own key or certificate request (below)
```

No kit holds a private key or node key: the hospital makes it with `./install.sh`, and only public material (or, for vantage6, the node key once over a secure channel) goes to the operator, who approves or registers it on central:

| Framework | The hospital sends | The operator runs on central | The hospital then |
|---|---|---|---|
| NVFlare | `nvflare/<site>.request.zip` and the public key hash `./install.sh` prints | `bash deploy/central/nvflare/approve.sh <site>.request.zip --expect-pubkey <hash>`; returns `signed/<site>.signed.zip` | puts the signed zip into `nvflare/` and runs `./install.sh` again (checks the root CA fingerprint in `kit.env`, builds the startup kit) |
| vantage6 | `credentials/api_key` and the fingerprint `./install.sh` prints | `bash deploy/central/vantage6/register_node.sh <site> <key file> --fingerprint <fp>` | runs `./start.sh` |
| Flower | `credentials/node_key.pub` and the sha256 `./install.sh` prints | `bash deploy/central/flower/register_node.sh add <site> <site.pub> --expect-sha256 <sha256>` | runs `./start.sh` (before the registration it stops with 'the SuperLink refused this node's key') |

Confirm each hash or fingerprint on a second channel (for example by phone): it ties the approval to what the hospital sent. Then, on the hospital machine:

```bash
./prepare_data.sh && ./start.sh && ./status.sh
```

**B. The testbed scripts** `deploy/<framework>-vm/`, which run both sites of a machine and a watchdog. Each machine makes its sites' credentials itself; only public material, or the vantage6 node key once, goes to central (on the testbed with `tools/bus put ... --to central`, which encrypts with the shared bus token: acceptable for the testbed only, see [docs/frameworks/vantage6.md](frameworks/vantage6.md)).

| Machine | Commands |
|---|---|
| `ubuntunvflare` | `bash deploy/nvflare-vm/request.sh nvf-h1` (and `nvf-h2`): send each `requests/<site>/<site>.request.zip` with its printed hash to central, which runs `deploy/central/nvflare/approve.sh <zip> --expect-pubkey <hash>` and returns `<site>.signed.zip` into `~/flhub-runtime/nvflare/incoming/`; write the root CA fingerprint central publishes to `~/flhub-runtime/nvflare/rootCA.fingerprint`; then `bash deploy/nvflare-vm/install.sh && bash deploy/nvflare-vm/start.sh && bash deploy/nvflare-vm/status.sh` |
| `ubuntuvantage` | `bash deploy/vantage6-vm/make_key.sh v6-h3` (and `v6-h4`): each sends its key encrypted to central and prints `fp=...`; on central, for each: `tools/bus get <id> -o <file> && bash deploy/central/vantage6/register_node.sh <site> <file> --fingerprint <fp>`; back here: `bash deploy/vantage6-vm/make_key.sh <site> --confirm <fp>`; then `bash deploy/vantage6-vm/install.sh && bash deploy/vantage6-vm/start.sh && bash deploy/vantage6-vm/status.sh` |
| `ubuntuflower` | `bash deploy/flower-vm/install.sh`; once per site `bash deploy/flower-vm/keygen.sh flw-h5` (and `flw-h6`): send each `~/flhub-runtime/flower/keys/<site>.pub` with its printed sha256 to central, which runs `register_node.sh add` (above); copy the CA certificate from your laptop: `scp -3 zpalanciya@145.38.205.58:flhub-runtime/flower/tls/ca.crt zpalanciya@145.38.187.20:flhub-runtime/flower/ca.crt` and compare its `sha256sum` with what `tls.sh` printed; then `bash deploy/flower-vm/start.sh`, `sleep 30; bash deploy/flower-vm/status.sh` (the scripts use TLS and the site's key as soon as `~/flhub-runtime/flower/ca.crt` exists) |

To switch from B to A, stop B first (`bash deploy/<framework>-vm/stop.sh`, which also disables its watchdog).

When `bash deploy/central/status.sh` lists all six base sites online, the federation is ready.

## 4. Train across the frameworks

On central, in `tmux` so that a dropped SSH connection does not stop the run (`Ctrl-b d` detaches, `tmux attach -t train` returns):

```bash
cd ~/AgnosticFL && . ~/venvs/hub/bin/activate && tmux new -s train
python -m hub.orchestrator run --config configs/final.yaml --only nvflare --rounds 3 --name test-nvflare   # one framework
python -m hub.orchestrator run --config configs/final.yaml --rounds 3 --name mixed-test                    # all three
python -m hub.orchestrator run --config configs/final.yaml --name mixed-6sites                            # 30 rounds
python -m hub.sim --config configs/final.yaml --compare ~/flhub-runtime/runs/mixed-6sites-latest          # equivalence
```

`configs/final.yaml` trains PneumoniaMNIST on all six sites. Reference (stage 1): about 8 s per round (each round waits for the slowest framework), test accuracy 0.8654, AUC 0.9247, final model `sha256:7fd3e2a4…`; with the same CPU type and pinned versions on every machine your run should reach the same hash. hello-pt (CIFAR-10): `configs/hello-pt.yaml`, steps in [hello-pt](hello-pt.md). Runs are written to `~/flhub-runtime/runs/<name>-<UTC time>/` (`<name>-latest` points to the last).

## 5. Watch the hospitals

| Framework | Live log on the hospital machine |
|---|---|
| NVFlare | `tail -f ~/flhub-runtime/nvflare/sites/nvf-h1/log.txt`; training happens in the relay job: `tail -f ~/flhub-runtime/nvflare/sites/nvf-h1/<job id>/log.txt` (kits: `flhub-nvf-h1/nvflare/nvf-h1/...`) |
| vantage6 | `docker logs -f vantage6-v6-h3-user`; every task starts a short-lived algorithm container: `watch -n1 docker ps` |
| Flower | `tail -f ~/flhub-runtime/flower/logs/supernode-flw-h5.log` (kits: `flhub-flw-h5/run/supernode.log`) |

`top` shows a short CPU burst per round. On central, the adapters log to `~/flhub-runtime/nvflare/server/logs/adapter.log`, `~/flhub-runtime/vantage6/adapter.log` and `~/flhub-runtime/flower/logs/adapter.log`. What every site did, per round, from the run's records:

```bash
cd ~/flhub-runtime/runs/mixed-6sites-latest
python3 - <<'EOF'
import json, glob
for f in sorted(glob.glob("rounds/r*.json")):
    r = json.load(open(f))
    print(f[7:12], " | ".join(f"{u['site_id']} ({u.get('framework')}): n={u['num_examples']} "
          f"loss={u['metrics'].get('train_loss', float('nan')):.4f} train={u['metrics'].get('train_time_s', 0):.1f}s"
          for u in r.get("updates", [])))
EOF
```

## 6. Develop your own algorithm

### 6.1 As a user of the repository: your own project folder

You do not need to change the repository's code to train your own model with hospitals. Keep your work in a folder of your own; the repository loads it as **plugins**: Python files that `deploy/central/plugins.sh` copies into `flhub_core/plugins/` (a folder git ignores). From there they travel with `flhub_core` to every hospital, through NVFlare's job, the vantage6 image and the Flower FAB, and they are part of the code version the hub checks at every site.

```
~/my-project/
  my_net.py        # your model, from flhub_core/src/flhub_core/plugins/_example_my_net.py
  my_data.py       # optional: your dataset (numpy only, no torch), see 6.5
  my-net.yaml      # your run configuration (it can live anywhere)
```

`my_net.py` defines the model class and registers it under the name the run configuration uses:

```python
from flhub_core.model import register_model
class MyNet(nn.Module): ...            # see the example file and the rules in 6.2
register_model("my-net", MyNet)
```

The whole collaboration, with the hospitals on kits ([3.5](#35-hospitals)):

| Step | Where | Command |
|---|---|---|
| 1. try it, without any framework | your laptop, in a clone of the repository ([section 2](#2-try-everything-on-one-machine)) | `bash deploy/central/plugins.sh ~/my-project`, once `python -m flhub_core.data manifests --variants cifar10-iid --out configs/stage2/partitions`, then `python -m hub.sim --config ~/my-project/my-net.yaml --rounds 3 --runs-dir /tmp/runs` |
| 2. bring your folder to the operator's machine | laptop | `scp -r ~/my-project zpalanciya@145.38.205.58:` |
| 3. install it and ship it to every hospital | central | `bash deploy/central/rollout.sh --plugins ~/my-project` |
| 4. once: a kit per hospital, sent over a secure channel | central, then laptop | `bash deploy/central/hospital_kits.sh`, then `scp -3 ...` ([3.5](#35-hospitals)) |
| 5. once: each hospital installs and starts its kit | each hospital | `./install.sh && ./prepare_data.sh && ./start.sh` |
| 6. train together | central | `python -m hub.orchestrator run --config ~/my-project/my-net.yaml` |
| 7. change the model and train again | laptop, central | edit `my_net.py`, steps 2, 3 and 6 again; **the hospitals do nothing** |

Kits contain no model, so they stay valid when your model changes; make new ones only for new hospitals, or when a plugin adds a public dataset the hospitals should prepare with `./prepare_data.sh`. `bash deploy/central/plugins.sh` without arguments lists the installed plugins, the models and datasets they add, and the code version; `--clear` removes them (then `rollout.sh` again). No commit is needed: the vantage6 image tag includes a hash of the plugins.

### 6.2 The rules

- All training code lives in `flhub_core` ([`flhub_core/src/flhub_core/`](../flhub_core/src/flhub_core/)). The per-framework clients (`clients/`) only call `flhub_core.train(weights, flhub_core.data.load_site(data), train_config)`; do not put training code there.
- The model's weights are its `state_dict`, all float32 (no BatchNorm: its counter is an integer buffer; use GroupNorm or LayerNorm).
- Training must be deterministic (`flhub_core.train` sets one CPU thread, deterministic algorithms and seeded data order); then a real run equals the simulation bit for bit, which is how you prove that every framework ran the same algorithm.
- Hospitals install nothing of the hub. Code reaches them as NVFlare job code, a vantage6 algorithm image tag, or the Flower FAB, when the operator rolls it out.
- Who changes `flhub_core`: users add models and datasets as plugins (6.1), with no change to the repository; changes to the library itself (training loop, partitions) go through a pull request that the operator reviews, commits and rolls out. Every site then reports the new `client_code_version`, and a run with `strict_code_version: true` accepts nothing else.

### 6.3 Where things go

| You want | Change | Selected in the run config by |
|---|---|---|
| a model | a plugin file with `register_model` (6.1), or a class in [`model.py`](../flhub_core/src/flhub_core/model.py) registered in `_ZOO` and `MODELS` | `model: <name>` |
| a dataset (shape, classes, public copy) | a plugin file with `register_dataset(DatasetInfo(...))`, or a `DatasetInfo` in `DATASETS` of [`data.py`](../flhub_core/src/flhub_core/data.py) | `dataset: <name>` |
| a split of the data over the sites | a partition in [`partition.py`](../flhub_core/src/flhub_core/partition.py) (`iid`, `dir0.3`, `qty`, ... exist) and in the dataset's `pids` | `partition: <pid>` |
| a training setting (epochs, batch, lr, momentum, weight decay, FedProx μ) | nothing: `train_config` keys are passed through | `train_config: {...}` |
| a different loss, optimizer or task | `_sgd()` in [`train.py`](../flhub_core/src/flhub_core/train.py) (keep it deterministic; branch on `cfg.model` or on a new `TrainConfig` key) | `model:` or the new key |
| a server-side aggregator | [`hub/aggregators/`](../hub/aggregators/) ([aggregators](aggregators.md)); central only, nothing to roll out | `aggregator: {name: ..., params: {...}}` |
| hub-side evaluation | the hub tests on the dataset's public test split | `eval: {every: 1}` (or `0` without a public copy) |

A run configuration (schema 2; [`configs/hello-pt.yaml`](../configs/hello-pt.yaml) is a complete one):

```yaml
schema: 2
name: my-run
dataset: cifar10
partition: iid
model: my-net
aggregator: {name: fedavg, params: {}}
train_config: {local_epochs: 1, batch_size: 32, lr: 0.01, momentum: 0.9}
seed: 2026
rounds: 20
sites: [nvf-h1, nvf-h2, v6-h3, v6-h4, flw-h5, flw-h6]   # which adapter serves a site: configs/stage2/topology.yaml
strict_code_version: true
```

The configuration is checked when loaded: the model's `input_shape` must match the dataset's `image_shape`, a dataset other than PneumoniaMNIST needs a partition, and a dataset without a public copy needs `eval: {every: 0}`.

### 6.4 Add a model

As a plugin (6.1) the class is the same and the file ends with `register_model("my-net", MyNet)` instead of the two zoo lines; [`plugins/_example_my_net.py`](../flhub_core/src/flhub_core/plugins/_example_my_net.py) is this model as a plugin. In the library itself:

```python
# flhub_core/src/flhub_core/model.py
class MyNet(nn.Module):
    """my-net: what it is, for which input."""

    input_shape = (3, 32, 32)   # (C, H, W); checked against the dataset

    def __init__(self, num_classes: int = 10, in_channels: int = 3) -> None:
        super().__init__()
        self.conv1 = nn.Conv2d(in_channels, 16, 3, padding=1)
        self.norm1 = nn.GroupNorm(4, 16)          # not BatchNorm
        self.fc = nn.Linear(16 * 16 * 16, num_classes)

    def forward(self, x: torch.Tensor) -> torch.Tensor:   # x: float32 (n, C, H, W), pixel / 255
        x = F.max_pool2d(F.relu(self.norm1(self.conv1(x))), 2)
        return self.fc(torch.flatten(x, 1))


MODELS = (..., "my-net")
_ZOO = {..., "my-net": MyNet}
```

Port an existing PyTorch model the way `hello-pt` ports NVFlare's `SimpleNetwork`: keep its layer names and creation order (then state dicts and seeded initial weights carry over), and move input normalization from the data loader into `forward()`.

### 6.5 Data: public datasets and hospital-owned data

A site file is a CSV per data variant: `~/flhub-data/<dataset>-<partition>-<site>.csv`, header `label,x0..x<F-1>`, integer pixels 0-255, integer class labels from 0. The width F gives the image shape: 784 = 1x28x28, 3072 = 3x32x32 (the R, G and B planes one after another, each row by row). A new dataset adds its shape; its width must differ from the other datasets'.

A dataset can be a plugin too: a file of its own that imports only numpy and calls `register_dataset(DatasetInfo(...))` with the arguments below (the hospitals' data tool runs without torch).

**A public dataset** (the hub keeps the test split and checks every site's file):

```python
# flhub_core/src/flhub_core/data.py, in DATASETS
"mydata": DatasetInfo("mydata", "https://.../mydata.npz", "<sha256 of that file>", 4,
                      ("class-a", "class-b", "class-c", "class-d"), ("iid", "dir0.3"),
                      image_shape=(1, 28, 28), in_all=False),
```

The npz holds `train_images`, `train_labels`, `test_images`, `test_labels` (and `val_*` if `splits` names it), images as `(n, H, W)` or channel-first `(n, C, H, W)`. Then on central `python -m flhub_core.data manifests --variants mydata-iid --out configs/stage2/partitions` pins each site's file (size, label histogram, sha256, fingerprint), and each hospital writes its share with `python -m flhub_core.data materialize --variants mydata-iid --sites <site> --out ~/flhub-data --manifests <dir>` (in a kit: `./prepare_data.sh mydata-iid`). CIFAR-10 is done this way.

**Data only the hospitals have** (the real case): register the dataset without a public copy, so the hub neither downloads it nor expects a manifest:

```python
"wards": DatasetInfo("wards", "", "", 3, ("low", "medium", "high"), ("own",),
                     image_shape=(1, 28, 28), in_all=False, splits=()),
```

Each hospital converts its own images into `~/flhub-data/wards-own-<site>.csv` (any tool; the format above), and the run configuration uses `dataset: wards`, `partition: own`, `eval: {every: 0}`. The hub then has no test set: judge the model by the sites' training metrics in the round records, or evaluate the final model (`~/flhub-runtime/runs/<run>/rounds/`) where a test set is allowed. A vantage6 node offers only the files that exist when it starts: after adding a file, `./stop.sh && ./start.sh` (kit) or `bash deploy/vantage6-vm/install.sh && bash deploy/vantage6-vm/stop.sh && bash deploy/vantage6-vm/start.sh`.

### 6.6 Test before anything runs at a hospital

1. **Unit test** next to [`tests/unit/test_hello_pt.py`](../tests/unit/test_hello_pt.py): shapes, parameter count, deterministic training. `python -m pytest`; `python -m flhub_core.selfcheck` must keep its digest unless you changed what it checks.
2. **Your data, one site, no federation** (a hospital can do this on its own machine):

   ```python
   from flhub_core.data import load_site
   from flhub_core.model import init_weights
   from flhub_core.train import train
   cfg = {"model": "hello-pt", "data_variant": "cifar10-iid", "local_epochs": 1, "batch_size": 32,
          "lr": 0.01, "momentum": 0.9, "seed": 202610, "round": 1}
   weights = init_weights(202610, "hello-pt", 10)
   weights, n, metrics = train(weights, load_site("~/flhub-data/cifar10-iid-nvf-h1.csv"), cfg)
   print(n, metrics)      # train_loss, train_acc, train_time_s, ...
   ```

3. **The federation without frameworks**: `python -m hub.sim --config my-run.yaml --runs-dir /tmp/runs` (all sites in one process; it downloads the public dataset and splits it in memory, so public datasets only).
4. **Each framework on one machine**: the three `run_local.py` examples of [section 2](#2-try-everything-on-one-machine) run the library's framework code paths with the default configuration.

### 6.7 Ship it

Plugins (6.1): `bash deploy/central/rollout.sh --plugins ~/my-project`, then the runs below with `--config ~/my-project/<run>.yaml`. A change to the library itself, after it is committed on central (`git pull`, or `git am` of a patch):

```bash
cd ~/AgnosticFL
bash deploy/central/rollout.sh            # NVFlare: job re-exported and resubmitted; vantage6: image built,
                                          # tagged with the commit, pushed; Flower: new FAB and relay run
bash deploy/central/status.sh             # all sites online again
python -m hub.orchestrator run --config my-run.yaml --rounds 3 --name my-run-3r
python -m hub.orchestrator run --config my-run.yaml
python -m hub.sim --config my-run.yaml --compare ~/flhub-runtime/runs/my-run-latest   # public data
```

Commit before `rollout.sh`: the vantage6 image tag is the commit (uncommitted changes give a `-dirty` tag). Nothing is needed at the hospitals, except the data file of a new variant. The per-round records show each site's `client_code_version`; with `strict_code_version: true` a site still on old code is rejected rather than averaged in.

### 6.8 Worked example: NVFlare's hello-pt

[hello-pt](hello-pt.md) is this whole procedure done once: NVFlare's `SimpleNetwork` became model `hello-pt` (same weights), CIFAR-10 became dataset `cifar10` with variants `cifar10-iid` and `cifar10-dir0.3`, hello-pt's optimizer settings became `train_config`, and its FedAvg job became `configs/hello-pt.yaml`. The same model then trained through NVFlare, vantage6 and Flower in one federation, equal to the simulation hash for hash.

## 7. For hospitals: your kit

The operator sends you `<site>.tgz`. It holds no secret of yours: you make your own credential with `./install.sh`, and your private key never leaves your machine. NVFlare: `./install.sh` makes your key and a certificate request; send the request and the public key hash it prints to the operator, put the signed answer into `nvflare/` and run `./install.sh` again, which checks the operator's root CA fingerprint from `kit.env` and builds your startup kit. vantage6: `./install.sh` makes `credentials/api_key` and prints its fingerprint; send the file once to the operator over a secure channel; the operator registers it (the server keeps only a hash). Flower: the kit holds the operator's CA certificate only; `./install.sh` makes your SuperNode key pair; send only `credentials/node_key.pub` and the sha256 it prints, and compare the CA's sha256 in `kit.env` with the one the operator gives you on a second channel. The kit also holds the address of the operator, pinned versions, and five scripts:

```bash
tar -xzf <site>.tgz && cd flhub-<site>      # where it should live; it stays mode 700
./install.sh        # the standard client of your framework in ./venv (NVFlare: also /etc/hosts; vantage6: Docker setup)
./prepare_data.sh   # demo data only (your share of CIFAR-10); real data: put your own <variant>-<site>.csv into ~/flhub-data
./start.sh          # starts the client and waits until it is connected
./status.sh         # HEALTHY = running and connected, plus your data files
./stop.sh
```

Requirements: Linux x86_64, bash, curl, outbound TCP to the operator on your framework's port; vantage6 needs Docker usable without sudo. What you never have to do: install the hub, install `flhub_core`, or update anything when the model, the training or the run settings change. What stays with you: the data (only weights leave the machine) and the decision to run the client. Overrides for testing: `FLHUB_DATA_DIR`, `FLHUB_CENTRAL_IP`, `FLHUB_PYTHON_VERSION`, `FLHUB_TORCH_INDEX_URL`.

## 8. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `uv: command not found` | uv not installed | `curl -LsSf https://astral.sh/uv/install.sh \| sh && source $HOME/.local/bin/env` |
| `docker: command not found` (central or vantage6 install) | no Docker | [3.2](#32-prerequisites) |
| `permission denied while trying to connect to the docker API` | the docker group is not active in this login | log out, SSH in again, `docker ps` |
| `AGENTBUS_TOKEN not set` (`tools/bus`) | the optional bus is not configured | send credentials with `scp -3` ([3.5](#35-hospitals)) |
| `-bash: id: No such file or directory` | `<id>` in a command is a placeholder; bash read `<` as a redirect | put the real value |
| `no startup kit for nvf-h1` | the hospital started before its certificate came back | central: `approve.sh`; the hospital: signed zip into `incoming/` (or the kit's `nvflare/`), rerun `install.sh` ([3.5](#35-hospitals)) |
| `approve: REFUSED: public key ... is not the one the hospital announced` | the request zip and the announced hash differ | ask the hospital for its hash again; never approve without a match |
| `nvflare package` `ROOTCA_FINGERPRINT_MISMATCH` | the signed zip is not from this operator's CA, or the pinned fingerprint is wrong | compare `rootCA.fingerprint` with what central published |
| `no node API key for v6-h3: run deploy/vantage6-vm/make_key.sh v6-h3` | the hospital has not made its key | `make_key.sh <site>`, central `register_node.sh`, then `make_key.sh <site> --confirm <fp>` |
| `... is not confirmed as registered` | central has not registered the key, or `--confirm` is missing | central `register_node.sh`, then `make_key.sh <site> --confirm <fp>` |
| vantage6 node log `Api key is not recognized` | the key is not registered, or was rotated away | central `register_node.sh` with the current key |
| Flower `start.sh`: 'the SuperLink refused this node's key'; log `[code: 2004] Failed to activate SuperNode` | the operator has not registered this site's key (or another one) | send `node_key.pub` + sha256; operator: `register_node.sh list`, `register_node.sh add` |
| Flower log `CERTIFICATE_VERIFY_FAILED` | the CA certificate is not the operator's | get `ca.crt` again; compare its sha256 |
| Flower site connected but `discover` shows it offline; relay log `claims site ... refused` | the node's key is registered for another site | register the right key for the site (`register_node.sh add <site> ... --replace`) |
| Flower `NOT connected`, `UNHEALTHY` right after start | not connected yet, or port 9092 blocked | wait 30 s and rerun `status.sh`; else [3.3](#33-firewall) |
| all ports `BLOCKED` from a hospital | no security-group rules on central | [3.3](#33-firewall) |
| `Invalid CIDR notation '145.38.189.142'` | the portal wants CIDR, one address per rule | `145.38.189.142/32`, one rule per port |
| rules added, still blocked | the rules are on a hospital's workspace | add them to the central workspace (FrameworkServer) |
| `central VM status: UNHEALTHY` with only `agent bus FAIL` | the bus is not running | ignore |
| central services also running on a hospital machine | `deploy/central/*` was run there | there: `bash deploy/central/flower/stop.sh; bash deploy/central/nvflare/stop.sh`, then `rm -rf ~/venvs ~/flhub-runtime ~/.config/vantage6/server` (never on central) and reinstall the hospital side |
| central's runtime deleted by mistake | the clean-up above ran on central | `pkill -f adapters.vantage6`, then rerun [3.4](#34-central) with `sudo systemctl restart flhub-hubcore` before `start.sh`; the CAs are new: every hospital requests a new NVFlare certificate (`request.sh` / `./install.sh`, central `approve.sh`), vantage6 keys are registered again (`register_node.sh`), Flower keys are registered again and the new `ca.crt` goes to the Flower hospitals |
| `no partition manifest .../cifar10-iid.json` | the hub pins each site's file of a public dataset | `python -m flhub_core.data manifests --variants cifar10-iid --out configs/stage2/partitions` on central |
| `model X takes 3x32x32 images, dataset Y has 1x28x28` | model and dataset do not fit | choose a matching model or dataset |
| a site's update rejected: `data_fp ... != manifest` | the site trained on other data than the manifest pins | rewrite the file (`prepare_data.sh` / `materialize --manifests`) |
| a site rejected for its code version | the site runs older `flhub_core` | `bash deploy/central/rollout.sh` (vantage6: the node pulls the new tag at its next task) |
| `unknown model 'my-net' ... plugins not loaded: ...` | a plugin file failed to import, or was not installed | `bash deploy/central/plugins.sh` shows the error; install with `plugins.sh ~/my-project` |
| `flower-superexec` / `flwr-clientapp` not found | the Flower venv's `bin` is not on PATH | use the start scripts (they set PATH) |

## 9. Reference

| Port | Where | Who connects |
|---|---|---|
| 8002 | central, public | NVFlare hospitals |
| 8003 | central, keep closed | NVFlare adapter (local) |
| 7601, 5050 | central, public | vantage6 nodes (server API, image registry) |
| 9092 | central, public | Flower SuperNodes (TLS; registered keys only) |
| 9093, 8800, 8811-8813 | central, `127.0.0.1` | SuperLink HTTP API, hub core, adapters |
| 7700 | central | optional agent bus |
| 9094, 9095 | Flower hospital, local | SuperNode runtime port per site |

| Path | What |
|---|---|
| `~/flhub-runtime/` | all run-time state (central and testbed hospitals); never in the repository |
| `~/flhub-runtime/runs/` | run directories (central) |
| `~/flhub-runtime/hospital-kits/` | kits (central); they hold no hospital secret |
| `~/flhub-runtime/nvflare/server/ca/` | NVFlare root CA (central, mode 700; its key never leaves) |
| `~/flhub-runtime/flower/tls/`, `~/flhub-runtime/flower/nodes.map` | Flower CA and SuperLink certificate (mode 700); registered SuperNodes: site, node id, public key sha256 |
| `~/flhub-data/` | site data and `raw/` downloads |
| `~/venvs/` | per-role virtual environments of the testbed scripts |
| `deploy/versions.env` | pinned versions: Python 3.11.17, torch 2.14.1 (CPU), numpy 2.4.4, NVFlare 2.9.0, vantage6 4.15.2, Flower 1.39.0 |

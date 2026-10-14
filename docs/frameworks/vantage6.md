# vantage6 guide

This guide covers the vantage6 adapter, the hospital-side vantage6 algorithm, and what it costs to run the framework-agnostic hub with vantage6. For the protocol, see [`protocol.md`](../protocol.md). For the framework-independent client contract, see [`client-contract.md`](../client-contract.md).

## 1. Summary

| | |
|---|---|
| Tested version | vantage6 **4.15.2**: server and node images `ghcr.io/vantage6/infrastructure/server:4.15.2` and `ghcr.io/vantage6/infrastructure/node:4.15.2`, server with its default SQLite database. Algorithm image on Python 3.11.17 with torch 2.14.1+cpu, numpy 2.4.4, safetensors 0.8.0 ([`deploy/versions.env`](../../deploy/versions.env)) |
| Protocol | `flhub/0.2` (also serves `flhub/0.1` callers) |
| Status | Tested in stage 1 (2 hospitals, `flhub/0.1`) and stage 2 (2 hospitals, plus 2 extra hospitals for the 12-site run; experiments E1–E7). Conformance suite v2 against the reference deployment: 13 of 13 tests pass, and every returned weight equals local training bit for bit (max abs diff 0.0). |
| Hospital code | [`clients/vantage6/v6_flhub/__init__.py`](../../clients/vantage6/v6_flhub/__init__.py), 43 lines, a standard vantage6 algorithm in its own image. 0 lines changed since stage 1. Nothing hub-specific is installed on the hospital host. |
| Rollout channel | A new tag of the algorithm image in the operator's registry. Nodes pull it at their next task: 0 hospital steps, 0 restarts. |
| Main limit | With the default SQLite server database the round time grows faster than linearly with the model size: 128 MiB is the largest model delivered (468 s per round), 256 MiB results never arrive (§6). |

The adapter is a small HTTP service next to the vantage6 server that logs in as an ordinary vantage6 user. For every hub round it creates one vantage6 task per hospital, which runs the `train` method of the operator's algorithm image with the global weights inline in the task input, and it hands each hospital's result to the hub as soon as that hospital's task completes.

Sources for this guide. Full stage reports at their tags: [stage-1 vantage6 section](https://github.com/Zare2001/AgnosticFL/blob/stage1-final/docs/report/vantage6.md), [stage-2 vantage6 section](https://github.com/Zare2001/AgnosticFL/blob/stage2-final/docs/report/stage2/vantage6.md), [stage-2 report](https://github.com/Zare2001/AgnosticFL/blob/stage2-final/REPORT_STAGE2.md). Decisions: ADR-004, ADR-005, ADR-013, ADR-014, ADR-015, ADR-020, ADR-021 and ADR-022 in [`design/decisions.md`](../design/decisions.md). Evidence: [`results/evidence/stage2/`](../results/evidence/stage2/) (tables, cost model, bytes on wire, robustness, [256 MiB node logs](../results/evidence/stage2/vantage6/e4a-256mb-node-logs.txt)), conformance reports [stage 1](../results/evidence/stage1/conformance-vantage6-central.json) and [stage 2](../results/evidence/stage2/conformance-v2-vantage6-central.json), and the [hospital ledger](../results/evidence/hospital-ledger.md). Line references into vantage6 (for example `node/__init__.py:579`) are to the 4.15.2 wheels `vantage6-node`, `vantage6-server` and `vantage6-client` on PyPI.

## 2. Server side: how the adapter maps flhub onto vantage6

### 2.1 Components

| Component | Code | Runs | Role |
|---|---|---|---|
| vantage6 server | standard image, started with `v6 server start` | central host; port 7601 | Hospital nodes connect to it. Stores tasks, runs and results in its database (SQLite by default). |
| Docker registry | `registry:2` | central host; port 5050 | Holds the algorithm image `flhub/v6-train:<tag>` that the nodes pull. |
| Adapter service | [`adapters/vantage6/service.py`](../../adapters/vantage6/service.py), `Vantage6Adapter(BaseAdapter)` | central host, `127.0.0.1:8812` | Serves flhub JSON-RPC to the orchestrator; talks to the vantage6 server with the vantage6 Python user client. |
| Algorithm image | [`clients/vantage6/`](../../clients/vantage6/) + `flhub_core` | in each hospital's algorithm container, pulled per task | Standard vantage6 algorithm around `flhub_core.train` (§3). |
| Hospital node | standard `ghcr.io/vantage6/infrastructure/node:4.15.2` | each hospital (Docker) | Connects out to the server and the registry; runs the algorithm on the hospital's database. |
| Native baseline | [`adapters/vantage6/native.py`](../../adapters/vantage6/native.py), [`deploy/central/vantage6/native/`](../../deploy/central/vantage6/native/) | central host, for the E7 comparison only | vantage6's idiomatic FedAvg as a central function. Not needed to run the hub. |

vantage6 entities ([`deploy/central/vantage6/entities.template.yaml`](../../deploy/central/vantage6/entities.template.yaml)):

- Organisation `hub-operator` with the users `root` (imported with a generated password; without a user `root` a fresh server creates `root`/`root`) and `hub-adapter` (the adapter's login).
- One organisation per hospital. **The site id is the organisation name**, for example `v6-h3`.
- Collaboration `flhub` with `encrypted: false`. Its participants are `hub-operator` and every hospital organisation; vantage6 creates a node record with an API key for each. `hub-operator` must be a participant because vantage6 lets only participating organisations create tasks. Its node is not started for hub runs.

### 2.2 One round, step by step

| flhub/0.2 | vantage6 4.15.2 user client (`vantage6.client.UserClient`) |
|---|---|
| login | `UserClient(url, port, api_path)`, `authenticate(user, password)`, `setup_encryption(None)`; `util.get_server_version()` gives `framework_version` |
| `discover` | `GET node?collaboration_id=<id>`: a site is `online` when its organisation's node has `status == "online"`. A site without an organisation is `offline`; a server that does not answer gives `unknown`. |
| `dispatch` | per active site, in parallel: `task.create(collaboration, organizations=[org_id], name="flhub-r<round>", image, description, input_={"method": "train", "kwargs": {"weights_b64", "train_config", "sites": {"<org id>": "<site id>"}}}, databases=[{"label": db_label(data_variant)}])` |
| `tasks/get` per site | `GET task/<id>/status` of each site's task |
| per-site result | when a site's task is `completed`: `result.from_task(id)`, checks, weights into the hub core, `report_site()` |
| per-site failure | run states `failed`, `start failed`, `non-existing Docker image`, `crashed`, `killed by user`, `not allowed`, `unknown error`: `GET run?task_id=<id>` for the run log, `report_site_error()` |
| `cancel`, `cancel {sites}`, deadline | `task.kill(id)` of every site's (or the given sites') task; vantage6 also marks pending runs `killed` |
| after the round | models ≥ 16 MiB: `task.delete(id)` for the round's tasks |

1. The orchestrator sends `dispatch` with the model reference, `train_config` and, for a data variant, the hint `data_variant`.
2. The adapter reads the model blob from the hub core and base64-encodes the exact bytes once. It never parses the weights (`needs_weights = False`).
3. Per site it creates one vantage6 task for that site's organisation, with the algorithm image and the database label of the data variant (`default` without a variant). The server stores the input per organisation as base64(json(input)): about 1.78× the model, because the weights are already base64 inside the JSON.
4. The node receives the run, checks or pulls the image (by default a node pulls for every task, `require_algorithm_pull`), starts the algorithm container with the named database, and `wrap_algorithm()` calls `train(...)` (§3).
5. The algorithm trains one round with `flhub_core.train` and returns JSON; the node sends the result to the server.
6. The adapter polls each site's task status. When it is `completed`, the adapter downloads the result, checks that `site_id` is the site and `base_model` is the round's model, stores the weights in the hub core as received and reports the site. The envelope metrics get `vantage6_create_s` (input encoding and upload), `vantage6_fetch_s` (result download and decoding) and `vantage6_store_s` (hub-core store).
7. A failed run becomes a site error that contains the last 600 characters of the run log; the other sites go on. A cancelled site, or a site still active at the deadline, is stopped with `task.kill`.
8. For models ≥ 16 MiB the adapter deletes the round's tasks once the round is settled, so the server database holds one round (`V6_PURGE_MIN_MB`).

Why one task per site ([ADR-013](../design/decisions.md)): in vantage6 4.15.2 a multi-organisation task turns `failed` as soon as one run fails, `/kill/task` then refuses to stop the other runs, and a run listing returns every run's full input. One task per site gives exact per-site state, per-site results and per-site cancel.

Sessions: one control session for status polls and kills, and one data session per site for uploads and downloads, so a long transfer never blocks the polls. Control calls give up after 2 connection errors (the client otherwise retries forever), so a vantage6 server outage only delays a round, and `discover` answers `unknown` meanwhile.

Polling: every 1 s. In database mode every status call makes the server load the run row with its input, so for large models the adapter polls each site every model-MiB / 16 s, at least 1 s and at most 30 s ([ADR-020](../design/decisions.md)).

### 2.3 Sites: identity, data variants and new hospitals

The adapter serves a fixed list of sites (`--sites`). At login it maps organisation names to ids, and it reads them again while a site is missing, so organisations registered later are picked up. A vantage6 algorithm only learns its organisation **id** (`meta.organization_id`). The task input therefore carries the map `sites: {"<org id>": "<site id>"}`, the algorithm returns `site_id`, and the adapter rejects a result whose `site_id` does not match.

Data variants ([ADR-014](../design/decisions.md)): an algorithm receives its data from the node as a DataFrame of the database the task names, so each data variant is one labelled database in the node configuration (§3.2). The label is `db_label(variant)`: the variant with every character outside `[A-Za-z0-9_]` replaced by `_` (`organamnist-dir0.3` → `organamnist_dir0_3`), because node database labels must be Python identifiers. The adapter takes the variant from the dispatch hint `data_variant` (`train_config` stays opaque); without a hint it uses the label `default`. The hub checks which data each site trained on through the `data_fp` metric.

New hospital: the reference script [`deploy/central/vantage6/add_sites.sh`](../../deploy/central/vantage6/add_sites.sh) calls, as `root`, `organization.create`, `collaboration.add_organization` and `node.create`, and writes the new node's API key to a mode-600 file for that hospital. Then add the site to `V6_SITES` and restart the adapter.

### 2.4 Ports and configuration

| Flag | Environment | Default | Meaning |
|---|---|---|---|
| `--host` | — | `127.0.0.1` | JSON-RPC bind address |
| `--port` | `ADAPTER_PORT` | `8812` | JSON-RPC port |
| `--sites` | `V6_SITES` | `v6-h3 v6-h4` | site ids = vantage6 organisation names |
| `--server-url` | `V6_SERVER_URL` | `http://127.0.0.1` | vantage6 server |
| `--server-port` | `V6_SERVER_PORT` | `7601` | |
| `--api-path` | `V6_API_PATH` | `/api` | |
| `--collaboration` | `V6_COLLABORATION` | `flhub` | collaboration name |
| `--username` | `V6_ADAPTER_USER` | `hub-adapter` | vantage6 user of the adapter |
| — | `V6_ADAPTER_PASSWORD` | required | the user's password; environment only, never on the command line |
| `--image` | `V6_ALGO_IMAGE` | required | algorithm image the nodes pull, `<registry-host>:5050/flhub/v6-train:<tag>` |
| `--hub` | `FLHUB_HUB_URL` | `http://127.0.0.1:8800` | hub core |
| — | `V6_PURGE_MIN_MB` | `16` | delete a round's tasks when the model has at least this many MiB; `0` = every round |

Ports in the reference deployment ([`deploy/topology.env`](../../deploy/topology.env)): the server (7601) and the registry (5050) are reachable from the hospitals only; the adapter (8812) and the hub core (8800) listen on `127.0.0.1`.

### 2.5 Start, stop and health (reference testbed)

| Script in [`deploy/central/vantage6/`](../../deploy/central/vantage6/) | What it does |
|---|---|
| `install.sh` | venv with the vantage6 CLI and client; secrets generated once (mode 600, outside the repository); server configuration from `server.template.yaml`; entities imported once; image built and pushed; `adapter.env` and the node-key file for the hospitals |
| `start.sh` | starts the server (`v6 server start -c <config> --user`), waits for `/api/version`, starts the adapter; restarts the adapter when `adapter.env` changed, for example after a new image tag |
| `status.sh` | exit 0 when the server answers, the image is in the registry, the adapter answers `discover` and the base sites are online |
| `stop.sh` | stops the adapter and the server |
| `restart_adapter.sh`, `restart_server.sh` | restart only the adapter (tasks in flight live in the server and survive), or only the server |
| `add_sites.sh`, `purge.sh [--vacuum]` | register hospitals (§2.3); delete all tasks between runs and optionally VACUUM the SQLite file |

The adapter answers `GET /health`, and `discover` lists every site as `online`, `offline` or `unknown`.

## 3. Client side: what the hospital code must contain

A hospital runs the standard vantage6 node. The hub-specific code is one algorithm package, [`clients/vantage6/v6_flhub/__init__.py`](../../clients/vantage6/v6_flhub/__init__.py), built into an image by [`clients/vantage6/Dockerfile`](../../clients/vantage6/Dockerfile). The node pulls the image from the operator's registry and runs it in its algorithm container. The essential lines follow, each marked as vantage6's standard pattern or as specific to flhub.

### 3.1 The algorithm

```python
from vantage6.algorithm.tools.decorators import data, metadata
from vantage6.algorithm.tools.util import info

@metadata
@data(1)
def train(df, meta, weights_b64: str, train_config: dict, sites: dict | None = None) -> dict:
```

- **vantage6 standard.** `@data(1)` passes the task's database as a pandas DataFrame (for CSV: `pd.read_csv(uri)` with default arguments, exact for integer pixels). `@metadata` passes `task_id`, `node_id` and `organization_id`. The other arguments are the `kwargs` of the task input; `wrap_algorithm()` calls the function named by the input's `method`.
- **flhub.** The three keyword arguments: `weights_b64` (the global model, base64 safetensors), `train_config` (the hub's training configuration plus `round`) and `sites` (organisation id → site id).

```python
    org_id = int(meta.organization_id)
    site_id = (sites or {}).get(str(org_id))
```

- **flhub.** vantage6 gives an algorithm its organisation id, not its name; the site id comes from the map the adapter sends.

```python
    raw = base64.b64decode(weights_b64)
    site_data = fdata.load_site(df)
    info(f"site={site_id} org={org_id} round={train_config.get('round')} n={len(site_data)}")
    new_weights, num_examples, metrics = ftrain.train(fweights.from_safetensors(raw), site_data, train_config)
```

- **flhub.** The algorithm does no training of its own: `flhub_core.data.load_site(df)` turns the DataFrame into the site's data, and `flhub_core.train.train()` runs exactly one deterministic local round with the hub's `train_config`.
- **vantage6 standard.** `info()` writes to the run log, which the node streams to the server line by line.

```python
    return {
        "weights_b64": fweights.to_b64(new_weights),
        "num_examples": int(num_examples),
        "metrics": {k: float(v) for k, v in metrics.items()},
        "site_id": site_id,
        "organization_id": org_id,
        "base_model": fweights.sha256_ref(raw),
        "client_code_version": flhub_core.code_version(),
    }
```

- **vantage6 standard.** The return value must be plain JSON (`wrap_algorithm()` writes it with `json.dumps`), so the weights are base64 and the metrics plain floats.
- **flhub.** The reply fields: `num_examples` (the FedAvg weight); every `flhub_core` metric (`train_loss`, `train_acc`, `train_time_s`, `data_load_s`, `peak_rss_mb`, `data_fp`); `site_id`; `base_model`, the sha256 reference of the bytes the site trained from (the adapter rejects a result trained from another model); and `client_code_version`, the content hash of the `flhub_core` package.

The image ([ADR-004](../design/decisions.md)):

```dockerfile
ARG BASE_IMAGE=python:3.11.17-slim-bookworm
FROM ${BASE_IMAGE}
RUN pip install --no-cache-dir "torch==${TORCH_VERSION}+cpu" --index-url "${TORCH_INDEX_URL}" \
 && pip install --no-cache-dir "numpy==${NUMPY_VERSION}" "safetensors==${SAFETENSORS_VERSION}" \
      "pandas==${PANDAS_VERSION}" "vantage6-algorithm-tools==${VANTAGE6_VERSION}"
COPY flhub_core /src/flhub_core
COPY clients/vantage6 /src/v6_flhub
RUN pip install --no-cache-dir --no-deps /src/flhub_core /src/v6_flhub && rm -rf /src
ENV PKG_NAME=v6_flhub
CMD ["python", "-c", "from vantage6.algorithm.tools.wrap import wrap_algorithm; wrap_algorithm()"]
```

- **vantage6 standard.** `PKG_NAME` plus `wrap_algorithm()` is the entry point of the official `algorithm-base` image, so nodes need no change.
- **Deviation.** The official `algorithm-base:4.15` is Python 3.10. This image starts from `python:3.11.17-slim-bookworm` and installs what the base image adds (`vantage6-algorithm-tools` 4.15.2, pandas 2.3.3), so that torch, numpy and safetensors are identical at every site. `python -m flhub_core.selfcheck` inside the image prints the same digest as the hub.
- **flhub.** `flhub_core` and the pinned numeric stack. The build context is the repository root; `Dockerfile.dockerignore` limits it to the two packages.

### 3.2 Data and data variants at the hospital

The node configuration lists the hospital's data as databases (reference template: [`deploy/vantage6-vm/node.template.yaml`](../../deploy/vantage6-vm/node.template.yaml)):

```yaml
databases:
- label: default                     # the site's base file
  uri: <data-dir>/<site>.csv
  type: csv
- label: organamnist_dir0_3          # one entry per data variant, label = db_label(variant)
  uri: <data-dir>/organamnist-dir0.3-<site>.csv
  type: csv
share_config: false
encryption:
  enabled: false                     # must equal the collaboration's `encrypted`
policies:
  allowed_algorithms:
  - '^<registry-host>:5050/flhub/v6-train(:[A-Za-z0-9._-]+)?(@sha256:[a-f0-9]{64})?$'
```

- `share_config: false` stops the node from publishing its configuration. With CSV databases that includes every column name (785 per file), which the 4.15.2 server deletes row by row on every reconnect (§7).
- The `allowed_algorithms` regular expression admits every tag and digest of the operator's image. vantage6 matches it with `re.match`; escape the dots of the registry host.
- A task that names a label the node does not have fails with vantage6's own error text.

### 3.3 Packaging and rollout

```bash
docker build -f clients/vantage6/Dockerfile \
  --build-arg TORCH_VERSION=2.14.1 --build-arg NUMPY_VERSION=2.4.4 \
  --build-arg SAFETENSORS_VERSION=0.8.0 --build-arg VANTAGE6_VERSION=4.15.2 \
  -t <registry-host>:5050/flhub/v6-train:<tag> .
docker push <registry-host>:5050/flhub/v6-train:<tag>
```

`deploy/central/vantage6/install.sh` does this with the pins of `deploy/versions.env`; the tag is the 12-character git SHA (with `-dirty` when `flhub_core` or `clients/vantage6` has uncommitted changes). The adapter names the image in every task (`V6_ALGO_IMAGE`), and `start.sh` restarts the adapter when the tag changed. Each node pulls the new tag at its first task with it. Rollback: start the adapter with the previous tag ([ADR-021](../design/decisions.md)).

Measured for the `flhub_core` 0.1 → 0.2 update in stage 2 (stage-2 report §4 and stage-2 vantage6 section §3, at the tag):

| Step | Who | Time |
|---|---|---|
| build and push `v6-train:<tag>` | operator | 98 s |
| restart the adapter on the new tag | operator | 2 s |
| pre-pull the new tag (optional; otherwise pulled at the first task) | hospital | 12.6 s |
| hospital steps, node restarts and downtime for the code update | hospital | 0, 0, 0 s |

Selfcheck v2 inside the pulled image, run with `--network none`, printed the hub's digest `sha256:ac074a0b…`, and every envelope carried `client_code_version` `0.2.0+fe357a1dbec1`.

### 3.4 Hospital-side change ledger

| When | Type | Change | Lines in hospital code | Node restarts |
|---|---|---|---|---|
| stage 1 | install and configuration | standard node: vantage6 4.15.2 CLI in a venv, Docker, node image; node configuration with server address and port, API key (from the operator), data path and `allowed_algorithms` = the operator's image; the operator's plain-HTTP registry in Docker's `insecure-registries` | 0 (no hub code on the host) | — |
| stage 2 | data | nine data-variant CSV files per site, sha256-checked against the partition manifests | 0 | 0 |
| stage 2 | configuration | 10 databases (`default` + one per variant) and `share_config: false`: 13.2 s, both nodes offline for 14.7 s | 0 | 1 per node |
| stage 2 | operations | clean-up of the task folders that nodes never delete (§6.2) | 0 | 0 |
| later code updates | — | a new image tag (§3.3) | 0 | 0 |

`git diff --stat stage1-final..stage2-final -- clients/vantage6` is empty. Sources: [stage-1 vantage6 section](https://github.com/Zare2001/AgnosticFL/blob/stage1-final/docs/report/vantage6.md) §4 and the [hospital ledger](../results/evidence/hospital-ledger.md).

### Checklist: your vantage6 client must …

1. be a standard vantage6 algorithm image: `PKG_NAME` names your package, `CMD` runs `wrap_algorithm()`, and the package has a function `train`;
2. take the data with `@data(1)` and the organisation id with `@metadata`;
3. accept the keyword arguments `weights_b64`, `train_config` and `sites`;
4. call `flhub_core.data.load_site(df)` and `flhub_core.train.train(weights, data, train_config)` unchanged, with no training, preprocessing or randomness of its own;
5. return only JSON types: `weights_b64`, `num_examples`, `metrics` (every `flhub_core` metric, as floats), `site_id` (from `sites`), `base_model` (sha256 reference of the received bytes) and `client_code_version`;
6. use the pinned Python, torch, numpy and safetensors versions, so that `python -m flhub_core.selfcheck` inside the image prints the hub's digest;
7. be pushed to a registry the nodes can reach, and be admitted by every node's `allowed_algorithms` policy;
8. run on nodes whose databases include `default` and one label for each data variant you use.

## 4. Deploying next to an existing vantage6 federation

The adapter is a vantage6 user, so an existing vantage6 4.15 server and its nodes need no new software.

1. **Organisation and user for the hub.** Create or choose an organisation for the hub operator and make it a participant of the collaboration the hub will use; only participants can create tasks. Create a user for the adapter in it. In the reference deployment the users come from the entities import, which gives them the superuser role; a narrower role was not tested.
2. **Collaboration.** It must be unencrypted (`encrypted: false`), because the adapter calls `setup_encryption(None)`; an encrypted collaboration needs adapter support (§8). Each node's `encryption.enabled` must equal the collaboration's `encrypted`.
3. **Algorithm image.** Build and push the image (§3.3) to a registry the nodes can reach. A plain-HTTP registry needs an `insecure-registries` entry in each hospital's Docker daemon, because the node pulls through the host's Docker socket. The node also supports registry logins (`docker_registries` in its configuration); this was not tested.
4. **Hospitals.** Each hospital adds the image to its `allowed_algorithms` policy and, for data variants, one database per variant (§3.2), then restarts its node once. Recommended: `share_config: false`.
5. **Adapter.** Start it next to the server with the settings of §2.4, with the password in `V6_ADAPTER_PASSWORD`. `discover` should list every site as `online`. Then run the conformance suite as the admission test, for example `pytest tests/conformance --adapter http://127.0.0.1:8812 --sites <site-a> <site-b>` (options `--variant <variant>` and `--restart-cmd "<command that restarts the adapter>"` cover the data-variant and restart tests; see [adding a framework](../adding-a-framework.md)).

| Credential | Created by | Kept in |
|---|---|---|
| adapter user password | operator | the adapter's environment, read from a mode-600 file |
| `root` password, server JWT secret | operator (`install.sh`) | mode-600 files on the operator host |
| node API key | the vantage6 server, when the node is created | the hospital's node configuration (`api_key`, mode 600) |
| registry credentials | registry owner | none in the reference deployment (plain HTTP, limited by the firewall) |

Network: the hospitals need outgoing access to the server port (7601) and the registry port (5050) only. The reference 4-VM deployment, including the hospital-side scripts in [`deploy/vantage6-vm/`](../../deploy/vantage6-vm/), is described in [`deployment.md`](../deployment.md).

## 5. Cost and efficiency for vantage6

## 6. Limits, known issues and knobs

## 7. Troubleshooting

## 8. Future work and upstream reports

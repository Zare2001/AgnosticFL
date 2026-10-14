# vantage6 guide

This guide covers the vantage6 adapter, the hospital-side vantage6 algorithm, and what it costs to run the framework-agnostic hub with vantage6. For the protocol, see [`protocol.md`](../protocol.md). For the framework-independent client contract, see [`client-contract.md`](../client-contract.md).

## 1. Summary

| | |
|---|---|
| Tested version | vantage6 **4.15.2**: server and node images `ghcr.io/vantage6/infrastructure/server:4.15.2` and `ghcr.io/vantage6/infrastructure/node:4.15.2`, server with its default SQLite database. Algorithm image on Python 3.11.17 with torch 2.14.1+cpu, numpy 2.4.4, safetensors 0.8.0 ([`deploy/versions.env`](../../deploy/versions.env)) |
| Protocol | `flhub/0.2` (also serves `flhub/0.1` callers) |
| Status | Tested in stage 1 (2 hospitals, `flhub/0.1`) and stage 2 (2 hospitals, plus 2 extra hospitals for the 12-site run; experiments E1–E7). Conformance suite v2 against the reference deployment: 13 of 13 tests pass, and every returned weight equals local training bit for bit (max abs diff 0.0). |
| Hospital code | [`clients/vantage6/v6_flhub/__init__.py`](../../clients/vantage6/v6_flhub/__init__.py), 38 code lines (54 with the Dockerfile, plus 11 configuration lines; [lines of code](../results/evidence/loc.md)), a standard vantage6 algorithm in its own image. 0 lines changed since stage 1. Nothing hub-specific is installed on the hospital host. |
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
| `tasks/get` per site | answered from the adapter's own per-site state, which a background loop keeps current by polling `GET task/<id>/status` of each site's task |
| per-site result | when a site's task is `completed`: `result.from_task(id)`, checks, weights into the hub core, `report_site()` |
| per-site failure | run states `failed`, `start failed`, `non-existing Docker image`, `crashed`, `killed by user`, `not allowed`, `unknown error`: `GET run?task_id=<id>` for the run log, `report_site_error()` |
| `cancel`, `cancel {sites}`, deadline | `task.kill(id)` of every site's (or the given sites') task; vantage6 sets the task's unfinished runs to `killed by user` (`server/resource/event.py:322-327`) |
| after the round | models ≥ 16 MiB: `task.delete(id)` for the round's tasks |

1. The orchestrator sends `dispatch` with the model reference, `train_config` and, for a data variant, the hint `data_variant`.
2. The adapter reads the model blob from the hub core and base64-encodes the exact bytes once. It never parses the weights (`needs_weights = False`).
3. Per site it creates one vantage6 task for that site's organisation, with the algorithm image and the database label of the data variant (`default` without a variant). The server stores the input per organisation as base64(json(input)): about 1.78× the model, because the weights are already base64 inside the JSON.
4. The node receives the run and pulls the image (a node tries to pull for every run; with the default `require_algorithm_pull: true` a failed pull fails the run instead of falling back to a local copy, `node/docker/task_manager.py:226-241, 335-337`), starts the algorithm container with the named database, and `wrap_algorithm()` calls `train(...)` (§3).
5. The algorithm trains one round with `flhub_core.train` and returns JSON; the node sends the result to the server.
6. The adapter polls each site's task status. When it is `completed`, the adapter downloads the result, checks that `site_id` is the site and `base_model` is the round's model, stores the weights in the hub core as received and reports the site. The envelope metrics get `vantage6_create_s` (input encoding and upload), `vantage6_fetch_s` (result download and decoding) and `vantage6_store_s` (hub-core store).
7. A failed run becomes a site error that contains the last 600 characters of the run log; the other sites go on. A cancelled site, or a site still active at the deadline, is stopped with `task.kill`.
8. For models ≥ 16 MiB the adapter deletes the round's tasks once the round is settled, so the server database holds one round (`V6_PURGE_MIN_MB`).

Why one task per site ([ADR-013](../design/decisions.md)): in vantage6 4.15.2 a multi-organisation task turns `failed` as soon as one run fails, `/kill/task` then refuses to stop the other runs, and a run listing returns every run's full input. One task per site gives exact per-site state, per-site results and per-site cancel.

Sessions: one control session for status polls and kills, and one data session per site for uploads and downloads, so a long transfer never blocks the polls. The status polls and the node listing pass `attempts_on_timeout=2`, so they give up at the third connection error (the client otherwise retries forever, `common/client/client_base.py:290-304`); a vantage6 server outage therefore only delays a round, and `discover` answers `unknown` meanwhile. Kills and deletes keep the client's unlimited retries.

Polling: every 1 s. In database mode every status call makes the server load the run row with its input, so for large models the adapter polls each site every model-MiB / 16 s, at least 1 s and at most 30 s ([ADR-020](../design/decisions.md)).

### 2.3 Sites: identity, data variants and new hospitals

The adapter serves a fixed list of sites (`--sites`). At login it maps organisation names to ids, and it reads them again while a site is missing, so organisations registered later are picked up. The run metadata gives a vantage6 algorithm only its organisation **id** (`meta.organization_id`); the name would need an extra server call. The task input therefore carries the map `sites: {"<org id>": "<site id>"}`, the algorithm returns `site_id`, and the adapter rejects a result whose `site_id` does not match.

Data variants ([ADR-014](../design/decisions.md)): an algorithm receives its data from the node as a DataFrame of the database the task names, so each data variant is one labelled database in the node configuration (§3.2). The label is `db_label(variant)`: the variant with every character outside `[A-Za-z0-9_]` replaced by `_` (`organamnist-dir0.3` → `organamnist_dir0_3`), because node database labels must be Python identifiers. A label that would still not be an identifier (it starts with a digit) gets the prefix `v_` in the adapter, which the hospital-side helper in `deploy/vantage6-vm/common.sh` does not add; avoid variant names that start with a digit. The adapter takes the variant from the dispatch hint `data_variant` (`train_config` stays opaque); without a hint it uses the label `default`. The hub checks which data each site trained on through the `data_fp` metric.

New hospital: the reference script [`deploy/central/vantage6/add_sites.sh`](../../deploy/central/vantage6/add_sites.sh) calls, as `root`, `organization.create`, `collaboration.add_organization` and `node.create`, and writes the new nodes' API keys to one mode-600 file on the operator host (`~/flhub-runtime/vantage6/v6-nodes-extra.env`), which the operator hands to the hospitals. Then add the site to `V6_SITES` and restart the adapter.

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

The adapter answers `GET /health`, and `discover` lists every site as `online`, `offline` or `unknown`. The central scripts take `V6_RT` (runtime directory), `V6_VENV` and `V6_SERVER_BIND` as overrides; the development setup uses them to run a second server on one VM.

Setup notes for vantage6 4.15.2 (stage-1 vantage6 section §3, [ADR-005](../design/decisions.md)):

- `v6 server new` and `v6 node new` are interactive only, so the reference scripts render both configurations from templates that follow the CLI's own ([`server.template.yaml`](../../deploy/central/vantage6/server.template.yaml), [`node.template.yaml`](../../deploy/vantage6-vm/node.template.yaml)). The server configuration pins the server image and sets `ui.enabled: false`.
- The images are on `ghcr.io/vantage6/infrastructure/`. A node's image tag defaults to the server's major.minor version, so the node configuration pins it with `images.node`.
- The entities import needs every organisation field (`name`, `domain`, `address1`, `address2`, `zipcode`, `country`, `public_key`). Each collaboration participant is `{name, api_key}`, where the API key must be a UUID, and becomes one node.

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

- **vantage6 standard.** `@data(1)` passes the task's database as a pandas DataFrame (for CSV: `pd.read_csv(uri)` with default arguments, exact for integer pixels). `@metadata` passes a `RunMetaData` record (`task_id`, `node_id`, `collaboration_id`, `organization_id` and the run's file paths). The other arguments are the `kwargs` of the task input; `wrap_algorithm()` calls the function named by the input's `method`.
- **flhub.** The three keyword arguments: `weights_b64` (the global model, base64 safetensors), `train_config` (the hub's training configuration plus `round`) and `sites` (organisation id → site id).

```python
    org_id = int(meta.organization_id)
    site_id = (sites or {}).get(str(org_id))
```

- **flhub.** The run metadata holds the organisation id, not its name; the site id comes from the map the adapter sends.

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
- **flhub.** The reply fields: `num_examples` (the FedAvg weight); every `flhub_core` metric (`train_loss`, `train_acc`, `train_time_s`, `data_load_s`, `peak_rss_mb`, `data_fp`); `site_id`; `base_model`, the sha256 reference of the bytes the site trained from (the adapter rejects a result trained from another model); and `client_code_version`, the `flhub_core` version plus a 12-hex content hash of its source files (for example `0.2.0+fe357a1dbec1`).

The image ([ADR-004](../design/decisions.md)):

```dockerfile
ARG BASE_IMAGE=python:3.11.17-slim-bookworm
FROM ${BASE_IMAGE}
# (lines 7-12 of the file declare ARG TORCH_VERSION, TORCH_INDEX_URL, NUMPY_VERSION,
#  SAFETENSORS_VERSION, VANTAGE6_VERSION and PANDAS_VERSION=2.3.3)
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

`deploy/central/vantage6/install.sh` does this with the pins of `deploy/versions.env`; the tag is the 12-character git SHA (with `-dirty` when `flhub_core` or `clients/vantage6` has uncommitted changes). The adapter names the image in every task (`V6_ALGO_IMAGE`), and `start.sh` restarts the adapter when the tag changed. Each node pulls the new tag at its first task with it ([ADR-021](../design/decisions.md)). Rollback (not tested): set `V6_ALGO_IMAGE` in `adapter.env` back to the previous tag and run `restart_adapter.sh`; the nodes pull that tag at their next task.

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

1. **Organisation and user for the hub.** Create or choose an organisation for the hub operator and make it a participant of the collaboration the hub will use; only participants can create tasks. Create a user for the adapter in it. In the reference deployment the users come from the entities import, which gives every imported user a role with all rules of the server (`server/controller/fixture.py:81-91`), so the adapter's password controls the whole vantage6 server. The adapter needs much less (§2.2): view collaborations, organisations and nodes; create, view and delete tasks; view their runs and results; and send events in the collaboration, because vantage6 checks `task.kill` (`/kill/task`) against the `event` resource's send rule (`server/resource/event.py:175-180`). A role limited to that was not tested.
2. **Collaboration.** It must be unencrypted (`encrypted: false`), because the adapter calls `setup_encryption(None)`; an encrypted collaboration needs adapter support (§8). Each node's `encryption.enabled` must equal the collaboration's `encrypted`.
3. **Algorithm image.** Build and push the image (§3.3) to a registry the nodes can reach. A plain-HTTP registry needs an `insecure-registries` entry in each hospital's Docker daemon, because the node pulls through the host's Docker socket. The node also supports registry logins (`docker_registries` in its configuration); this was not tested.
4. **Hospitals.** Each hospital adds the image to its `allowed_algorithms` policy and, for data variants, one database per variant (§3.2), then restarts its node once. Recommended: `share_config: false`.
5. **Adapter.** Start it next to the server with the settings of §2.4, with the password in `V6_ADAPTER_PASSWORD`. `discover` should list every site as `online` (`deploy/central/vantage6/status.sh` does this on the testbed). Then run a few rounds and check each site's `client_code_version` in the round records of the run directory (`rounds/rNNNN.json`); the orchestrator also logs a warning when a site runs other `flhub_core` code than the hub, and with `strict_code_version: true` in the run configuration it rejects such updates.
6. **Admission test.** The conformance suite compares every returned update with local training on the same data, so it accepts only the project's benchmark sites. For vantage6 these are two test organisations named `v6-h3` and `v6-h4` whose nodes serve, as database `default`, the files that `python -m flhub_core.data materialize --sites v6-h3 v6-h4` writes. Then run `pytest tests/conformance --adapter http://127.0.0.1:8812 --sites v6-h3 v6-h4`. `--restart-cmd "<command that restarts the adapter>"` adds the restart test, and `--variant <variant>` the data-variant test, for which the test nodes also need that variant's database (§3.2; files from `python -m flhub_core.data materialize --variants <variant> --sites v6-h3 v6-h4 --manifests configs/stage2/partitions`). See [adding a framework](../adding-a-framework.md). The suite tests the adapter and the algorithm image, not the real hospitals.

| Credential | Created by | Kept in |
|---|---|---|
| adapter user password (on the testbed: all rules of the server, see step 1) | operator | the adapter's environment, read from a mode-600 file |
| `root` password, server JWT secret | operator (`install.sh`) | mode-600 files on the operator host |
| node API key | the operator: `install.sh` generates a UUID per node for the entities import; nodes added later through the API (`add_sites.sh`, `node.create`) get a key from the server | the hospital's node configuration (`api_key`, mode 600); the operator keeps copies in mode-600 files |
| registry credentials | registry owner | none in the reference deployment (plain HTTP, limited by the firewall) |

Network: the hospitals need outgoing access to the server port (7601) and the registry port (5050) only. The reference 4-VM deployment is described in [`deployment.md`](../deployment.md). Its hospital-side scripts, in [`deploy/vantage6-vm/`](../../deploy/vantage6-vm/):

| Script | What it does |
|---|---|
| `install.sh --keys <file>` | installs the vantage6 4.15.2 CLI in a venv and pulls the node image; adds the operator's registry to `insecure-registries` (restarts Docker once); writes one node configuration per hospital from the operator's node keys; adds the watchdog and clean-up cron entries |
| `start.sh`, `stop.sh`, `status.sh` | start the nodes with `v6 node start --user` and wait until they are connected; stop them with `docker stop`; exit 0 when both are connected |
| `taskdir_gc.sh` | deletes finished `task-<run id>` folders whose newest file is older than 5 min and whose algorithm container is not running |
| `extra.sh` | the two extra hospitals of the 12-site run |
| `inject.sh`, `straggler.sh`, `netem.sh`, `sysmon.sh`, `run_follow.py`, `endstate.sh` | experiment tools: fault injection and CPU throttling (E6), link shaping (E4b), monitoring, end-state check |
| `island.sh`, `island_round.py` | a development setup on one VM |

The watchdog cron entry re-runs `start.sh` every minute while the nodes are enabled; after `docker kill` of a node container the node was back and connected after 15 s (stage-1 vantage6 section §8).

## 5. Cost and efficiency for vantage6

Comparison with the other frameworks: [`cost-and-efficiency.md`](../cost-and-efficiency.md).

### 5.1 At a glance

| Dimension | vantage6 | Source |
|---|---|---|
| hospital code changed in stage 2 | 0 lines | [hospital ledger](../results/evidence/hospital-ledger.md) |
| code rollout | new image tag: 98 s build and push, 2 s adapter restart; 0 hospital steps | §3.3 |
| hub vs vantage6's own FedAvg (E7) | 0.00 s (0 %) with `cnn-small`; −13.4 s (−14 %) with a 45 MiB model; weights hash-identical | [tables](../results/evidence/stage2/tables.md) |
| fixed cost per round | 6.3 s (1 MiB echo model) | [tables](../results/evidence/stage2/tables.md) |
| cost per MiB | 1.49 s/MiB (fit on 1–64 MiB), superlinear above | [cost model](../results/evidence/stage2/cost_model.md) |
| bytes on the wire ÷ payload | 4.97–5.53 up to 64 MiB (design 5.34: five input copies down, one result up); 9.08 at 128 MiB | [bytes on wire](../results/evidence/stage2/e4_bytes.md), §5.4 |
| bandwidth efficiency at 20 Mbit/s | 0.16 against the 4·P bound; 0.87 against vantage6's own volume of 21.36·P (both directions) | [cost model](../results/evidence/stage2/cost_model.md), §5.4 |
| largest model delivered | 128 MiB (468.2 s per round); 256 MiB never delivered | §6.1 |
| 6 → 12 sites (E5) | fan-out 9.1 → 10.4 s | [tables](../results/evidence/stage2/tables.md) |
| killed training container (E6a) | detected after 2.1 s, round done 15.2 s after the kill | [robustness](../results/evidence/stage2/e6.md) |

### 5.2 Implementation and integration cost

| Item | Size |
|---|---|
| adapter, `adapters/vantage6/` without `native.py` | 290 code lines (`service.py` 288); 14 distinct vantage6 client calls, counting the `UserClient` constructor and three raw `request()` endpoints |
| hospital algorithm, `clients/vantage6/v6_flhub/__init__.py` | 38 code lines |
| image and package metadata, `clients/vantage6/Dockerfile`, `pyproject.toml` | 16 code lines, 11 configuration lines |
| unit tests, `tests/unit/test_vantage6_adapter.py` | 225 code lines; 10 tests against a fake vantage6 client, which run without vantage6 installed |
| reference deployment, `deploy/central/vantage6/` and `deploy/vantage6-vm/` | 28 files, 1,162 code lines and 107 configuration lines, including the experiment tools |
| E7 baseline tooling, `adapters/vantage6/native.py` and `deploy/central/vantage6/native/` | `native.py` 198 code lines; not needed by the adapter |
| vantage6 concepts used | user, organisation, collaboration, node, task, run, result, database label, algorithm image, node policy |

Code lines are non-blank, non-comment lines with docstrings counted ([lines of code](../results/evidence/loc.md), `python3 scripts/docs/loc.py`; per-file values from the same counter).

At the hospitals, 0 lines of hospital code changed between stage 1 and stage 2. Hospital steps in stage 2 (§3.3, §3.4): (a) code rollout of `flhub_core` 0.2: 0 steps, a new image tag (optional pre-pull 12.6 s); (b) data provisioning for the data variants: data only; (c) data-variant configuration: one database per variant in the node configuration, 1 restart per node, 14.7 s offline; (d) later code updates: 0 steps.

### 5.3 Runtime overhead

**Against vantage6's own FedAvg (E7).** Native: a vantage6 central function on a node of the hub operator's organisation, which creates one multi-organisation `train` subtask per round and averages in float64 ([ADR-022](../design/decisions.md)). Hub: the adapter. Same two hospitals, same training code and configuration. Median round time over rounds ≥ 2, with the interquartile range ([tables](../results/evidence/stage2/tables.md); ranges from the stage-2 vantage6 section §5):

| Configuration | Native | Hub | Hub − native | Weights |
|---|---|---|---|---|
| C1 `cnn-small`, iid, 20 rounds | 8.81 s (8.74–8.88) | 8.81 s (8.56–9.56) | 0.00 s (0 %) | global hash equal in all 21 checkpoints |
| C2 `payload-45` (45 MiB), 5 rounds | 95.5 s (94.3–98.5) | 82.1 s (81.1–86.5) | −13.4 s (−14 %) | global hash equal in all 6 checkpoints |

The hub path is faster at 45 MiB: the central function moves every input and result through the node's proxy and waits on one multi-organisation subtask, while the adapter uploads per site in parallel and fetches each result as soon as that site is done. Hub aggregation took 0.0–0.07 s per round, the native aggregation 0.002–0.56 s.

![E7: native FedAvg vs hub](../results/figures/e7_native_vs_hub.png)

**Where a round's time goes (E3).** Median over rounds ≥ 2 for the vantage6 sites in the FedAvg runs ([tables](../results/evidence/stage2/tables.md)):

| Model | Parameters | Training (s) | Transport, vantage6 and adapter (s) | Fan-out (s) | Overhead share |
|---|---|---|---|---|---|
| `cnn-small` | 52,203 | 1.84 | 7.23 | 9.12 | 79 % |
| `cnn-medium` | 1,676,779 | 13.47 | 14.08 | 28.71 | 49 % |
| `resnet18-gn` | 11,173,323 | 225.26 | 41.99 | 267.58 | 16 % |

vantage6 set the round time of the mixed federation for every model. Its overhead also grows during a run while tasks stay in the server database: with `cnn-medium` from 8.7 s at round 3 to 12.4 s at round 21 (stage-2 report §6.3). With `resnet18-gn` the adapter deletes each round's tasks (≥ 16 MiB) and the overhead stays flat.

![E3: round-time breakdown](../results/figures/e3_round_time_breakdown.png)

**Stage 1.** With `flhub/0.1` (one vantage6 task for both organisations per round) and the 51,618-parameter PneumoniaMNIST CNN, the vantage6-only run took 238 s for 30 rounds, 7.8 s per round, against 2.5 s for NVFlare and 7.3 s for Flower ([stage-1 report](https://github.com/Zare2001/AgnosticFL/blob/stage1-final/REPORT.md) §7, [stage-1 results](../results/stage1.md)).

**Fixed cost per task.** With a 1 MiB echo model a vantage6 round takes 6.3 s ([tables](../results/evidence/stage2/tables.md)). Node-log phases per task, mean of 50 `cnn-small` rounds: 3.3 s from "Starting task" to the start of training (image check, container start, Python and torch import, reading the database), 1.9 s training, 2.3 s from the output to the result being sent (stage-2 vantage6 section §5).

**Cost model.** Fan-out time per round = a + b·P, fitted on the E4a echo sweep, P in MiB ([cost model](../results/evidence/stage2/cost_model.md)):

| Fit | a (s) | b (s/MiB) | R² |
|---|---|---|---|
| linear, 1–64 MiB (used for predictions) | 4.13 | 1.494 | 0.959 |
| linear, 1–128 MiB | 3.65 | 1.680 | 0.581 |
| with a quadratic term, 1–128 MiB (c = 0.019 s/MiB²) | | | 0.981 |

Predictions with the 1–64 MiB fit plus the slowest site's training: −31 to −38 % for `cnn-small`, −3 to −4 % for `cnn-medium`, +10 % for `resnet18-gn`. For small models the line underestimates the fixed cost per task (a = 4.13 s against 6.3 s measured at 1 MiB), and real training adds about 0.8 s per task that the echo runs do not have: reading the CSV database into a DataFrame, and a slower container exit after a torch run ([stage-2 report](https://github.com/Zare2001/AgnosticFL/blob/stage2-final/REPORT_STAGE2.md) §6.6). The cost per MiB is 1.49 s against 0.021 s for NVFlare and 0.061 s for Flower, and growth above 64 MiB is superlinear (§6.1).

### 5.4 Network efficiency

**Bytes on the wire** (interface counters on the hospital VM ÷ nominal payload, which counts each site's model once down and once up per round; [bytes on wire](../results/evidence/stage2/e4_bytes.md)):

| Model size | 1 MiB | 8 MiB | 32 MiB | 64 MiB | 128 MiB |
|---|---|---|---|---|---|
| ratio, vantage6 only | 4.97 | 5.49 | 5.53 | 5.37 | 9.08 |

By design a node receives each run's input (1.78× the model) five times per site and round: in the run listing that starts it (`node/__init__.py:404`), in the server's responses to its three PATCH requests for that run (start time `:448`, status `:502`, result `:621`; the server returns the whole run, input included, `server/resource/run.py:716`), and in an extra `GET run/<id>` before it sends the result (`:598`). That is 8.9× the model down and 1.78× up per site and round, a design ratio of (8.9 + 1.78) ÷ 2 = 5.34 against the nominal payload. Measured per site and round: 9.1× down and 1.9–2.0× up at 8 and 32 MiB, 8.8× down and 1.95× up at 64 MiB, close to the design; at 128 MiB about 16× down, because the client retried requests after the busy server dropped the connection (stage-2 vantage6 section §4). The stage-2 analysis counted four input copies, without the start-time PATCH, and therefore put the design at 4.45× and attributed the measured excess over it to retries.

**Bandwidth (E4b).** 45 MiB model, mixed federation, `netem` rate and delay on the link between central and the hospital VM in both directions (1 Gbit/s with 5 ms, 100 Mbit/s with 20 ms, 20 Mbit/s with 40 ms; [cost model](../results/evidence/stage2/cost_model.md), `deploy/vantage6-vm/netem.sh`). The stage-2 lower bound is 4·P/rate: each link carries two sites, 2·P down before training and 2·P up after it. Counted the same way, vantage6 moves 2 sites × (5 input copies + 1 result) × 1.78 = 21.36·P per link and round. Efficiency = bound ÷ measured fan-out:

| Profile | Fan-out (s) | 4·P/rate (s) | Efficiency | 21.36·P/rate (s) | Efficiency against vantage6's own volume |
|---|---|---|---|---|---|
| no shaping | 81.8 | 0 | — | 0 | — |
| 1 Gbit/s | 83.3 | 1.5 | 0.02 | 8.1 | 0.10 |
| 100 Mbit/s | 129.5 | 15.1 | 0.12 | 80.6 | 0.62 |
| 20 Mbit/s | 463.8 | 75.5 | 0.16 | 403.2 | 0.87 |

[cost_model.md](../results/evidence/stage2/cost_model.md) uses V = 17.8 for vantage6, derived from four input copies plus the result. With the five copies of the node source, 17.8·P is the downstream volume alone (2 × 5 × 1.78); its E4b predictions and the efficiency of 0.72 at 20 Mbit/s (336.0 s ÷ 463.8 s) count only that direction.

With limited bandwidth vantage6 dominates a mixed round: 463.8 s at 20 Mbit/s, against 80.3 s for NVFlare and 85.1 s for Flower ([tables](../results/evidence/stage2/tables.md)).

![E4b: measured fan-out vs lower bound](../results/figures/e4b_measured_vs_bound.png)

### 5.5 Memory and payload size

| E4a echo model | 1 MiB | 8 MiB | 32 MiB | 64 MiB | 128 MiB | 256 MiB |
|---|---|---|---|---|---|---|
| median round, vantage6 only (s) | 6.3 | 12.8 | 60.1 | 115.3 | 468.2 | not delivered (§6.1) |
| vantage6 fan-out in the mixed federation (s) | 6.4 | 13.2 | 58.6 | 115.4 | 392.3 | testbed disk limit |
| peak RSS of one training container (MiB, `peak_rss_mb`) | 303 | | | 573 | 851 | |
| peak memory used on the hospital VM, 2 sites (MiB, sysmon) | 1,792 | 1,973 | 2,362 | 3,322 | 5,282 | |
| peak RSS of the vantage6 server on the central host (MiB) | 262 | 396 | 756 | 1,315 | 2,349 | 2,524 |
| peak RSS of the vantage6 adapter on the central host (MiB) | 99 | 234 | 464 | 906 | 1,925 | 3,627 |

Sources: [tables](../results/evidence/stage2/tables.md), [vantage6 VM memory](../results/evidence/stage2/vantage6/e4a-vm-memory.md) (also per node and for the mixed runs), [central host memory](../results/evidence/stage2/central-e4a-memory.md) and, for `peak_rss_mb`, the stage-2 vantage6 section §5. From 32 to 128 MiB the vantage6 server needed 18–24× the payload and the adapter 14–15×, while the hub core stayed at 42–45 MiB (47 MiB at 256 MiB). Where a site's time goes (median of rounds 2–3, stage-2 vantage6 section §5): at 128 MiB the result arrived after 467 s, of which 0.05 s training, 33–38 s in the adapter (`vantage6_create_s` 10–11 s, `vantage6_fetch_s` 15–21 s, `vantage6_store_s` 1–2 s) and about 430 s inside vantage6 (server database rows, node download and upload, log streaming); at 64 MiB, 115 s = 0.03 s + 13–17 s + about 100 s.

Disk: a 4.15.2 node never deletes its per-run folders `task-<run id>` (input and output, about 2.7× the model per site and round), and the server database grows with every stored task. See §6 for the clean-up.

![E4a: round time vs payload](../results/figures/e4a_round_time_vs_payload.png)

![E4a: peak RSS vs payload](../results/figures/e4a_peak_rss_vs_payload.png)

### 5.6 Scaling and robustness

**Scaling (E5).** Six sites against twelve (four per framework; in `iid12` every site holds half as much data as in `iid`, 2,880 instead of 5,760 examples, so the total stays the same; `configs/stage2/partitions/`): vantage6 fan-out 9.1 → 10.4 s ([tables](../results/evidence/stage2/tables.md)), overhead per site 6.7 → 8.2 s, hospital-VM CPU mean 26.7 → 42.0 % with four training containers on 4 vCPUs (stage-2 report §6.4). The two extra nodes were online 16 s after the request; registered but offline, they never blocked a run.

![E5: scaling](../results/figures/e5_scaling.png)

**Robustness (E6)** ([robustness](../results/evidence/stage2/e6.md)):

| Fault | Detection | Recovery | Attempts of that round | Manual action |
|---|---|---|---|---|
| training container killed in round 10, all sites required | 2.1 s (run `crashed`) | round done 15.2 s after the kill | 2 | none |
| the same with `min_sites: 5`; an NVFlare site was still out, so 4 sites remained and the round was retried with all 6 | 2.1 s | 32.3 s | 2 | none |
| vantage6 server restarted between rounds (back after 14.8 s) | — | next round done 26.9 s after the restart | 1 | none |
| vantage6 server restarted during round 12 (back after 14.5 s) | — | next round done 20.4 s after the restart | 1 | none |
| site throttled to 10 % CPU, rounds 7–13, deadline 20 s | — | dropped in all 7 rounds by per-site `task.kill`; rounds 20.3 s | 1 | none |
| orchestrator and adapters killed (SIGKILL) in round 21, run resumed | — | adapter back after 2.2 s (stage-2 report §7); round 21 done 20.1 s after the kill | 1 | none |

vantage6 reports a killed algorithm container at once (the run becomes `crashed`), so its detection is the fastest of the three frameworks. During both server restarts the nodes reconnected by themselves, and no vantage6 round failed or was retried. A node restarted in the middle of a round runs its open run again after reconnecting (the server hands open runs back), so the round completes without a retry; in a development test that round took 19.6 s instead of 8 s ([ADR-015](../design/decisions.md)).

## 6. Limits, known issues and knobs

### 6.1 Measured limits (vantage6 4.15.2, SQLite server database)

| Limit | Value | Cause | Source |
|---|---|---|---|
| largest model delivered | 128 MiB, 468.2 s per round | per-log-line row rewrites (§6.2) | [tables](../results/evidence/stage2/tables.md) |
| 256 MiB | never delivered: each node trained round 1 six times in 32 min and every result was dropped, until the round was cancelled | for every streamed log line the server rewrites the run row (its input alone is 1.78 × 256 MiB ≈ 456 MiB), the node's websocket times out, and the node drops the result (§6.2) | [node logs](../results/evidence/stage2/vantage6/e4a-256mb-node-logs.txt) |
| largest storable model | about 536 MiB; 1024 MiB cannot be stored | SQLite rejects a value > 10⁹ bytes (999,999,000 bytes accepted, 1,000,000,001 rejected), and a run's input is 1.78× the model | [ADR-020](../design/decisions.md); stage-2 vantage6 section §4 |
| growth with model size | superlinear: 64 → 128 MiB costs 4.1× the time | per-log-line row rewrites | [tables](../results/evidence/stage2/tables.md) |
| bytes on the wire | 5–9× the payload | five input copies per site and round, 1.78× encoding; retries at 128 MiB | §5.4 |
| fixed cost per round | about 6.3 s | image check, container start, task and result round trips | §5.3 |

### 6.2 Known issues

- **A result is dropped when the websocket is down.** After training, the node takes the result from its queue and first sends a status event over the websocket (`node/__init__.py:575-579`). If the socket is disconnected, the emit fails, the exception is caught ("Speaking thread had an exception") and the result is lost. When the node reconnects, the server hands the still-open run back and the node trains again. This is the 256 MiB failure.
- **Run rows are rewritten per log line.** The server's `on_algorithm_log` loads the run, appends the line and saves the whole row (`server/websockets.py:442-444`); with SQLite this rewrites the row including its input. About 24 log lines per round blocked the single server worker for about 60 s at 64 MiB.
- **Overhead grows with the stored runs.** `input` is the first column of the run table (`server/model/run.py:58-68`), so in SQLite every later column of a row sits behind its input, and listings filtered on those columns walk all stored inputs.
- **Task listings load all stored data.** `GET /task` computes each task's status from its runs, including their input and result. With 100 stored `cnn-medium` tasks the listing held the single server worker for 12 min. Delete tasks by id instead.
- **Deleting a task with subtasks orphans the subtasks' runs** (`server/resource/task.py:1289-1301`). Their input and result stay in the database, unreachable through the API; after the E7 native runs these were 50 rows, and the database file stayed at 1.6 GiB after deleting every task and a VACUUM (`du -h` in `purge.sh`).
- **Nodes never delete task folders** (`node/docker/task_manager.py:450-452`). A clean-up next to the node must age a folder by its newest file, because run ids are reused after task deletes and a reused folder keeps its old modification time ([`deploy/vantage6-vm/taskdir_gc.sh`](../../deploy/vantage6-vm/taskdir_gc.sh)).
- **The large result store is not usable in 4.15.2.** The client uploads blobs in chunks of exactly 1 MiB, which the server's uWSGI rejects at its default 1 MiB chunked-input limit; with the limit raised, the SQLite-backed server deadlocked while streaming blobs ([ADR-020](../design/decisions.md)).
- **Only unencrypted collaborations.** The adapter calls `setup_encryption(None)`.
- **Transport and storage in the reference deployment.** The vantage6 API and the registry are plain HTTP, limited by the cloud firewall, and the collaboration is unencrypted, so the server stores task inputs and results (the weights) in plaintext.

### 6.3 Knobs

| Knob | Side | Effect | Status |
|---|---|---|---|
| `V6_PURGE_MIN_MB` (default 16) | adapter | deletes a round's tasks for models of at least this size; `0` keeps the database at one round for every model and avoids the growing overhead | used (default) |
| poll interval | adapter (constant) | model-MiB / 16 s, 1–30 s, in database mode | used |
| `deadline_s`, `min_sites` | hub | stragglers and dropouts are cancelled per site | used |
| `purge.sh --vacuum` | operator | deletes all tasks and compacts the SQLite file between runs | used |
| `share_config: false` | hospital (node configuration) | the node does not publish its configuration | used |
| `share_algorithm_logs: false` | hospital (node configuration) | the node stops streaming algorithm log lines (`node/docker/task_manager.py:955-957`), which removes the per-line row rewrites; the operator no longer sees run logs | not tested |
| PostgreSQL as server database | server | a run update does not rewrite the large input, which PostgreSQL stores out of line | not tested |
| `large_result_store` | server | inputs and results in blob storage | not usable in 4.15.2 |

## 7. Troubleshooting

- **`v6 server import` reports success, but logins fail; the server logs "No root user found! Is this the first run?"** → On Python 3.11 the 4.15 CLI formats a str-Enum used as an environment-variable name as `ServerGlobals.DB_URI_ENV_VAR` instead of `VANTAGE6_DB_URI`, so the import writes into a throw-away database → run the server image's own `vserver-local import` with `VANTAGE6_DB_URI` set, as `install.sh` does ([ADR-005](../design/decisions.md)).
- **`v6 server stop` answers "flhub is not running!"** → the same Enum bug names the container `…-ServerType.V6SERVER` → stop the server container by its Docker labels (`stop.sh`).
- **A fresh server has the superuser `root`/`root`** → the server creates it when no user `root` exists → import `root` with a generated password.
- **`v6 node stop` and other CLI calls exit with "Can't write to log dir"** → server and node containers write the CLI's log directory as root → start the server with `-c <config>` (no CLI file log) and stop a node with `docker stop vantage6-<site>-user`.
- **The adapter's own log lines disappear** → `UserClient()` replaces the root logger's handlers and level → the adapter restores them under a lock.
- **The server is busy for minutes after a node (re)connects** → the node publishes every CSV column as a configuration row (785 per file), and the server deletes them one commit at a time → `share_config: false` in the node configuration.
- **`v6 node start` exits with "Database label … contains invalid characters"** → labels must be Python identifiers (letters, digits and underscores, not starting with a digit) → use `db_label(variant)`.
- **A run fails because a database is missing** → the task names a label the node does not have → add the variant's database to the node configuration and restart the node.
- **Runs end as `not allowed` (node log "Docker image … is not allowed on this Node!") or as `non-existing Docker image` (node log "Failed to pull image!")** → the image does not match the node's `allowed_algorithms`, or the node cannot pull it, for example from a plain-HTTP registry that is missing in `insecure-registries` (with the default `require_algorithm_pull` a local copy is not used) → fix the regular expression or the Docker daemon configuration.
- **Rounds take about 2 min at 64 MiB, the server runs at 100 % CPU, and nodes log "Connection error... Retrying"** → per-log-line row rewrites in SQLite (§6.2) → expected in database mode; the client retries and the rounds complete. Use smaller models or the untested knobs of §6.3.
- **A node logs `IncompleteRead` after a status update** → uWSGI cut the response (the whole run with its input) while the worker was busy; the update itself reached the server → harmless.
- **At 256 MiB the nodes train the same round again and again; their logs show "Speaking thread had an exception" and `BadNamespaceError: /tasks is not a connected namespace`** → the result is dropped while the websocket is down (§6.2) → no fix in 4.15.2 with SQLite; stay at or below 128 MiB.
- **A run stays `active`; the node logs `FileNotFoundError .../task-000000001/output`** → a task-folder clean-up deleted a fresh output: run ids are reused after deletes and the reused folder kept an old modification time → age folders by their newest file and skip folders whose container is running; the hub's deadline and retry recover the round.
- **The hospital disk fills up** → nodes never delete `task-<run id>` folders (about 2.7× the model per site and round) → a periodic clean-up such as `taskdir_gc.sh`.
- **Blob uploads fail with HTTP 500, then the server hangs** (`large_result_store` configured) → 1 MiB upload chunks against uWSGI's 1 MiB chunked-input limit, then a deadlock in the SQLite-backed server → do not use the large result store with 4.15.2.
- **Listing tasks takes minutes and the server needs GBs of memory** → `GET /task` loads every run's input and result → delete tasks by id; `purge.sh --vacuum` between runs.
- **The server database keeps growing after central-function tasks were deleted** → subtask runs are not deleted with their tasks → remove the orphaned rows while the server is stopped ([`scripts/stage2/v6_orphan_cleanup.sh`](../../scripts/stage2/v6_orphan_cleanup.sh)).

## 8. Future work and upstream reports

What would lift the limits:

1. A PostgreSQL server database, so that run updates do not rewrite the stored input (server side; not tested).
2. `share_algorithm_logs: false` on the nodes (hospital side; not tested; the operator loses the run logs).
3. A newer vantage6 (5.x) may change the fixed cost per task; not tested, because the project pins 4.15.2 ([ADR-002](../design/decisions.md)).
4. A blob store for inputs and results, once the client's upload chunks fit the server's uWSGI limit and the server runs on a database other than SQLite (with SQLite it deadlocked while streaming blobs, [ADR-020](../design/decisions.md)).
5. Adapter support for encrypted collaborations: the adapter organisation's private key in `setup_encryption`. This is a code change.
6. Production transport: an HTTPS reverse proxy for the server, a registry with TLS and authentication, and the image pinned by digest in each task (the node policy already accepts `@sha256:`) or an algorithm store.
7. Task-folder clean-up inside the node, after the result is sent.

Draft upstream issues for vantage6 4.15.2 (not filed):

> **Node downloads each run's full input five times.** A node receives the run input (base64 of the JSON input) in the run listing that starts the run (`node/__init__.py:404`), in the responses to its three PATCH requests for that run (start time via `set_task_start_time` at `:448`, status at `:502`, result at `:621`; `server/resource/run.py:716` returns `run_schema.dump(run)`, and `RunSchema` excludes only `result`), and in an extra `GET run/<id>` before it sends the result (`node/__init__.py:595-598`, marked `FIXME: why are we retrieving the result *again*?`). With model weights as input this is 8.9× the model downstream per run. We measured 8.8–9.1× downstream per run at 8–64 MiB and about 16× at 128 MiB, where requests were retried. Proposal: leave `input` out of PATCH responses (or return only the changed fields) and drop the extra GET; the node would then download the input once.

> **A finished run's result is lost when the websocket is down.** In `node/__init__.py:575-579` the node takes the result from its queue (`get_result()`) and emits `algorithm_status_change` before it PATCHes the result. If the socket is disconnected, for example because the server is busy, the emit raises, the exception is caught and logged as "Speaking thread had an exception", and the result is dropped. After reconnecting, the server returns the still-open run and the node executes it again. With 256 MiB inputs this repeated six times per node in 32 minutes, and no result was delivered. Proposal: PATCH the result first and emit afterwards, or keep the result until the PATCH succeeded and tolerate a failed emit.

> **The server rewrites the whole run row for every algorithm log line.** `on_algorithm_log` (`server/websockets.py:442-444`) loads the run, appends the line and calls `run.save()`. With SQLite this rewrites the row including its input (1.78× the model when the input carries weights). About 24 log lines per round blocked the single server worker for about 60 s at 64 MiB, and the cost grows with the input size times the number of lines. Proposal: store log lines in their own table or batch them, and keep large columns out of the run row (`input` is the first column in `server/model/run.py`, so scans on later columns also walk the inputs).

Smaller findings worth reporting: `Task._delete_subtasks()` deletes child tasks without their runs (`server/resource/task.py:1289-1301`); the 4.15 CLI's str-Enum formatting on Python ≥ 3.11 breaks `v6 server import` and `v6 server stop` ([ADR-005](../design/decisions.md)); the client's 1 MiB blob-upload chunks are rejected by uWSGI's default 1 MiB chunked-input limit; nodes never delete `task-<run id>` folders (`node/docker/task_manager.py:450-452`).

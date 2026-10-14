# vantage6 example

## 1. What this shows

- **[`client/`](client/)**: the minimal vantage6 algorithm that meets the [client contract](../../docs/client-contract.md), every essential line commented, and its image. `run_local.py` runs it without a vantage6 node, through vantage6's own `MockAlgorithmClient`, and checks the result bit for bit against `flhub_core.train`.
- **[`server/`](server/)**: how the vantage6 side of the hub is put together. `run_adapter.py` builds the real vantage6 adapter, a `BaseAdapter` without a relay, from its settings and serves it; `--check` runs it without a vantage6 server. `run-config.yaml` is the run configuration through which the orchestrator finds the adapter.

Both import the library (`flhub_core`, `adapters.*`) and reimplement nothing. Versions as tested: vantage6 4.15.2, Python 3.11.17, torch 2.14.1+cpu ([`deploy/versions.env`](../../deploy/versions.env)).

## 2. Client

| File | What |
|---|---|
| [`client/v6_example/__init__.py`](client/v6_example/__init__.py) | the algorithm: one method, `train` |
| [`client/pyproject.toml`](client/pyproject.toml) | package metadata (package `v6_example`) |
| [`client/Dockerfile`](client/Dockerfile), [`client/Dockerfile.dockerignore`](client/Dockerfile.dockerignore) | the algorithm image; build context is the repository root |
| [`client/run_local.py`](client/run_local.py) | runs `train` without a node and checks its result |

Where the items of the client contract are:

| Contract item | In `v6_example/__init__.py` |
|---|---|
| receive the global weights | keyword argument `weights_b64`: base64 safetensors in the task input |
| receive `train_config` (with the round) | keyword argument `train_config`; for a data variant the adapter picks the node database by label, so `df` already holds that variant |
| run `flhub_core.train` | `fdata.load_site(df)` and `ftrain.train(...)`, unchanged |
| return the weights, `num_examples`, the metrics (with `data_fp`) and the code version | `weights_b64`, `num_examples`, `metrics`, `client_code_version` |
| vantage6 only: prove site and base model | `site_id` from the `sites` map and `base_model`, the sha256 reference of the received bytes; the adapter checks both |

vantage6's standard pattern: `@metadata`, `@data(1)`, `info()`, a plain-JSON return value, and `PKG_NAME` plus `wrap_algorithm()` in the image. Specific to flhub: the three keyword arguments, the site-id mapping, the two `flhub_core` calls and the reply fields. Differences to the tested client in [`clients/vantage6`](../../clients/vantage6/): the package name, the comments, and no `organization_id` in the reply (the adapter does not use it).

Run it from the repository root, in the environment of [getting started](../../docs/getting-started.md#install) plus three packages:

```bash
uv pip install --python .venv/bin/python pandas==2.3.3 vantage6-algorithm-tools==4.15.2 vantage6-client==4.15.2
. .venv/bin/activate
python examples/vantage6/client/run_local.py
```

The script writes the benchmark partition of `v6-h3` (it downloads the public PneumoniaMNIST file, 4 MB, into a temporary directory; `--data-dir DIR` keeps it), reads it with `pd.read_csv` as `@data(1)` does on a node, gives `@metadata` a run token and the run's file paths as a node does, runs `train` through `MockAlgorithmClient`, and checks the reply as the adapter and the conformance suite do:

```
info > site=v6-h3 round=1 n=785
site v6-h3: num_examples 785, train_loss 0.617726, max abs diff 0.0e+00, client_code_version 0.2.0+fe357a1dbec1
  OK   site_id
  OK   base_model
  OK   num_examples
  OK   data_fp
  OK   client_code_version
  OK   weights equal flhub_core.train
PASS
```

The training loss equals that of `v6-h3` in round 1 of the conformance suite against the real deployment (0.6177260386716029 in [`conformance-v2-vantage6-central.json`](../../docs/results/evidence/stage2/conformance-v2-vantage6-central.json)). On a node you never call `train` yourself: the node runs the image for every task, and `wrap_algorithm()` calls the method named in the task input.

The image: build it with the command at the top of the [Dockerfile](client/Dockerfile), push it to the registry your nodes pull from, and admit it in each node's `allowed_algorithms` policy ([guide](../../docs/frameworks/vantage6.md) §3.2–§3.3).

## 3. Server

The vantage6 adapter ([`adapters/vantage6/service.py`](../../adapters/vantage6/service.py)) is a `BaseAdapter`. A vantage6 *user* can create tasks and read their results through the server's API, so no relay has to run inside the vantage6 server (NVFlare and Flower need one: `RelayAdapter`, see [adding a framework](../../docs/adding-a-framework.md)). The adapter implements `site_status()` (node status per organisation), `run_round()` (one vantage6 task per site, each result reported as soon as it is there) and `on_cancel()` / `on_cancel_sites()` (`task.kill`); the mapping is in the [guide](../../docs/frameworks/vantage6.md) §2.

| File | What |
|---|---|
| [`server/run_adapter.py`](server/run_adapter.py) | builds the real `Vantage6Adapter` from its settings and serves it; `--check` |
| [`server/run-config.yaml`](server/run-config.yaml) | run configuration: the adapter's URL and its sites, as the orchestrator reads them |

Settings: `--server-url`, `--server-port`, `--api-path` (the vantage6 server); `--username` and the environment variable `V6_ADAPTER_PASSWORD` (the adapter's vantage6 user, in an organisation that takes part in the collaboration); `--collaboration` (unencrypted); `--image` (the algorithm image of §2); `--sites` (the hospitals' organisation names); `--hub` (the hub core); `--host`, `--port` (the JSON-RPC endpoint). `python -m adapters.vantage6` is the same adapter with these settings as environment variables ([guide](../../docs/frameworks/vantage6.md) §2.4).

The hub core: the adapter reads each round's model from it and stores each site's update in it unchanged. Nothing registers with the hub core; the orchestrator finds the adapter through the `adapters` list of its run configuration.

```bash
python examples/vantage6/server/run_adapter.py --check
```

```
adapter on http://127.0.0.1:34235: /health {"ok": true, "name": "vantage6-adapter"}
discover: adapter vantage6, framework vantage6 unknown, protocol flhub/0.2 (supports flhub/0.1, flhub/0.2)
  sites: v6-h3=unknown, v6-h4=unknown
  capabilities: {"dtypes": ["float32"], "evaluate": false, "max_payload_mb": 1024, "min_round_interval_s": 0, "partial_collect": true, "payload": "full_weights", "site_cancel": true, "site_progress": true}
PASS
```

Before these lines the adapter logs two warnings: the login on the free port is refused, so `discover` reports the sites as `unknown`, the documented answer while the vantage6 server cannot be reached.

With a vantage6 server set up as in the [guide](../../docs/frameworks/vantage6.md) §4 and a hub core on port 8800:

```bash
export V6_ADAPTER_PASSWORD=...   # never on the command line
python examples/vantage6/server/run_adapter.py --server-url http://<central-host> \
    --image <registry-host>:5050/flhub/v6-example:<tag> --sites v6-h3 v6-h4
python -m hub.orchestrator run --config examples/vantage6/server/run-config.yaml --runs-dir <dir>
```

`python -m hub.sim --config examples/vantage6/server/run-config.yaml --runs-dir <dir>` runs the framework-free reference of the same configuration and needs no adapter; `--compare <run dir>` then checks a real run against it. With `--rounds 30` this reference ends with exactly the final model of the stage-1 vantage6-only run (§4).

## 4. Tested

On 2026-10-14, in a fresh clone of branch `examples/library-examples` at `57c1eeb` with a fresh virtual environment, on one CPU-only VM (AMD EPYC-Rome, AVX2): Python 3.11.17, torch 2.14.1+cpu, numpy 2.4.4, safetensors 0.8.0, pyyaml 6.0.3, pandas 2.3.3, vantage6-algorithm-tools 4.15.2, vantage6-client 4.15.2 (with vantage6-common 4.15.2 and pyjwt 2.12.1).

```bash
git clone --branch examples/library-examples https://github.com/Zare2001/AgnosticFL.git && cd AgnosticFL
uv venv --python 3.11.17 .venv
uv pip install --python .venv/bin/python "torch==2.14.1+cpu" --index-url https://download.pytorch.org/whl/cpu
uv pip install --python .venv/bin/python numpy==2.4.4 safetensors==0.8.0 pyyaml==6.0.3 pytest -e ./flhub_core -e .
uv pip install --python .venv/bin/python pandas==2.3.3 vantage6-algorithm-tools==4.15.2 vantage6-client==4.15.2
. .venv/bin/activate
export FLHUB_DATA_DIR=/tmp/v6-example-data
python examples/vantage6/client/run_local.py
python examples/vantage6/server/run_adapter.py --check
python -m hub.sim --config examples/vantage6/server/run-config.yaml --runs-dir /tmp/v6-example-runs
python -m hub.sim --config examples/vantage6/server/run-config.yaml --rounds 30 --runs-dir /tmp/v6-example-runs
python -m pytest
```

| Command | Result |
|---|---|
| `run_local.py` | PASS, 6 of 6 checks; 785 examples, training loss 0.617726, max abs diff 0.0, code version `0.2.0+fe357a1dbec1` |
| `run_adapter.py --check` | PASS; `/health` ok; `discover` answers with protocol `flhub/0.2` and both sites `unknown` (no vantage6 server: both login attempts were refused within 3 ms) |
| `hub.sim` with `run-config.yaml` | 3 rounds, 2 of 2 sites in each; final test accuracy 0.625, the share of the majority class, which the federated stage-1 runs also kept for their first 10 rounds ([stage-1 results](../../docs/results/stage1.md)) |
| the same with `--rounds 30` | final model `sha256:c6249b00…`, test accuracy 0.8638, AUC 0.9246: the final model of the stage-1 vantage6-only run, which used these two sites and settings ([`final-runs.json`](../../docs/results/evidence/stage1/final-runs.json)) |
| `python -m pytest` | 80 passed, 15 skipped (your venv has pandas, as in [CONTRIBUTING](../../CONTRIBUTING.md#development-setup); without pandas, as in getting started, 79 passed, 16 skipped); the examples add no tests |

Not tested here: building the image (the reference testbed builds no images in this stage; the Dockerfile has the steps of `clients/vantage6/Dockerfile`, which built the image of both test stages) and a run against a live vantage6 server (the adapter and the client were tested against the real deployment in both stages: [guide](../../docs/frameworks/vantage6.md) §1).

## 5. Links

- [`clients/vantage6/`](../../clients/vantage6/): the tested client this example is derived from
- [`adapters/vantage6/`](../../adapters/vantage6/): the adapter
- [vantage6 guide](../../docs/frameworks/vantage6.md): mapping, deployment, cost and efficiency, limits, troubleshooting
- [Client contract](../../docs/client-contract.md): what every hospital-side client must do

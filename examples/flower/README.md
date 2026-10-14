# Flower example

## 1. What this shows

How the code of a Flower hospital must look so that the hub can train with it, and how the hub's server side for Flower is built. Everything here is small, annotated and runs without the reference testbed.

| File | What it is |
|---|---|
| [`client/client_app.py`](client/client_app.py) | the minimal Flower ClientApp of the [client contract](../../docs/client-contract.md), in one file you can copy |
| [`server/adapter.py`](server/adapter.py) | the Flower adapter: thin, annotated launcher of the library's `FlowerAdapter` (a `RelayAdapter`), with a `--check` mode |
| [`server/server_app.py`](server/server_app.py) | the relay ServerApp: thin, annotated entry point of the library's relay loop |
| [`server/config.toml`](server/config.toml) | the adapter's `flwr` connection to the SuperLink (`$FLWR_HOME/config.toml`) |
| [`run_local.py`](run_local.py) | runs client and server end to end in real Flower 1.39 on one machine and checks the result against the framework-free simulation |

The library code these files build on is [`clients/flower/`](../../clients/flower/) and [`adapters/flower/`](../../adapters/flower/); the [Flower guide](../../docs/frameworks/flower.md) explains it in full.

## 2. Client

[`client/client_app.py`](client/client_app.py) is a standard Flower ClientApp with two handlers, `@app.train()` and `@app.query()`. It does what [`clients/flower/flhub_flower/client_app.py`](../../clients/flower/flhub_flower/client_app.py) and [`records.py`](../../clients/flower/flhub_flower/records.py) do, in one file. Comments mark every essential line as Flower's standard Message-API pattern (`[Flower]`) or as what the hub needs (`[flhub]`).

| Client-contract item | Where in `client_app.py` |
|---|---|
| receive the global weights | `msg.content["arrays"]`, an `ArrayRecord` keyed by the model's `state_dict` names; names and order kept |
| receive `train_config` with the hub `round` (and an optional `data_variant`) | `msg.content["config"]`, a `ConfigRecord`, passed to `flhub_core.train` unchanged |
| site identity | the SuperNode's node config `site-id`; answered to the relay's site-info QUERY in `@app.query()` |
| site data and data variants | node config `data-path`, else `$FLHUB_DATA_DIR/<site-id>.csv`; `load_site()` reads `<variant>-<site>.csv` next to it when a `data_variant` is set |
| train | `flhub_core.train(weights, load_site(data_path), train_config)`, the only training code |
| return the new weights | `"arrays"`: an `ArrayRecord`, same names and order, float32 |
| return `num_examples` | `MetricRecord` `"metrics"`, key `num-examples` (the FedAvg weight) |
| return the metrics, incl. `data_fp` | the other keys of the same `MetricRecord`, as `flhub_core.train` returns them |
| return the code version | `ConfigRecord` `"site-info"`, key `client-code-version` = `flhub_core.code_version()` |

Flower's standard pattern: `ClientApp`, the `@app.train()` and `@app.query()` handlers, `Message`, `RecordDict`, `ArrayRecord`, `MetricRecord`, `ConfigRecord`, `Message(reply, reply_to=msg)` and `context.node_config`. The `"arrays"` and `"config"` keys and the `num-examples` weight are also the defaults of Flower's own `FedAvg`. Specific to the hub: the `flhub_core.train` call, the `site-info` record, and the QUERY handler, which lets the relay map the random node ids of the SuperNodes to hub site ids.

**Use it in your own Flower app.** Put `client_app.py` into the Flower app that the adapter ships as a FAB (`--app-dir`) and name it in the app's `pyproject.toml`; the adapter adds `flhub_core` to the FAB:

```toml
[tool.flwr.app.components]
serverapp = "server_app:app"   # the hub's relay: server/server_app.py (or flhub_flower.server_app:app)
clientapp = "client_app:app"   # this file
```

A hospital then runs only the standard SuperNode, with flwr, torch, numpy and safetensors installed at the versions of [`deploy/versions.env`](../../deploy/versions.env):

```bash
flower-supernode --insecure --superlink <central-host>:9092 --port 9094 \
    --node-config "site-id='flw-h5' data-path='/path/to/flw-h5.csv'"
```

**Run it.** `python examples/flower/run_local.py` starts two such SuperNodes on one machine with this ClientApp (section 4).

## 3. Server

**Structure.** The hub talks to every framework through an adapter that speaks its protocol (flhub/0.2: discover, dispatch, tasks/get, collect, cancel). [`BaseAdapter`](../../adapters/common/base.py) serves that protocol; a framework that can be driven from outside implements `run_round()` on it directly, as the vantage6 adapter does. Flower cannot: messages to SuperNodes can only be sent from a ServerApp inside a Flower run. The Flower adapter is therefore a [`RelayAdapter`](../../adapters/common/relay.py): `run_round()` queues the round, and a *relay* ServerApp inside the run takes it (`GET /relay/next`), sends one TRAIN message per site and posts every site's result back.

- [`server/adapter.py`](server/adapter.py) builds the library's `FlowerAdapter` in three steps: `stage_fab()` packs the Flower app and `flhub_core` into one FAB directory and returns its content id (fab id); the adapter serves the protocol and the `/relay/*` routes on one local port; `RelayLauncher` runs `flwr run` whenever no relay is alive and replaces a relay that stops beating.
- [`server/server_app.py`](server/server_app.py) is the relay ServerApp: `@app.main()` hands the `Grid` to the library's `Relay`. Its docstring lists what one pass of the loop does.
- [`server/config.toml`](server/config.toml) is the `flwr` connection the adapter uses to submit and stop the relay run: the SuperLink's HTTP API, not its Fleet API.

**Settings.**

| Setting | Where | Default |
|---|---|---|
| port of the adapter (`127.0.0.1`) | `adapter.py --port` | 8813 |
| hub site ids, equal to the SuperNodes' `site-id` | `adapter.py --sites` | `flw-h5 flw-h6` |
| hub core URL | `adapter.py --hub` | `http://127.0.0.1:8800` |
| Flower app for the FAB | `adapter.py --app-dir` | `clients/flower` |
| `FLWR_HOME` with `config.toml`; the FAB goes to `../fab-src` | `adapter.py --flwr-home`, `--connection` | `~/flhub-runtime/flower/flwr-home`, `flhub` |
| adapter URL, hub URL, fab id for the relay | run config, set by the adapter | — |
| poll and QUERY timeouts of the relay | `[tool.flwr.app.config]` of the app's `pyproject.toml` | 5 s, 60 s |

**Hub core.** The adapter uses the hub core only as its blob store: the relay reads the global model there and stores each site's weights there; the protocol carries references. Nothing registers with the hub core: the orchestrator finds the adapter through its run configuration, for example (as `run_local.py` writes it):

```yaml
adapters:
  - {name: flower, url: "http://127.0.0.1:8813", sites: [flw-h5, flw-h6]}
```

**Check and run.** `python examples/flower/server/adapter.py --check` starts the adapter on a free port without Flower, calls `/health` and `discover`, and stops it; without a relay every site is `unknown`. Next to a SuperLink: `python examples/flower/server/adapter.py --hub <hub core URL> --flwr-home <dir with config.toml>`. The relay runs in the SuperLink's ServerApp runtime and imports the hub package (`adapters.common`), which the FAB does not carry: install it in the environment from which the SuperLink starts its ServerApps.

## 4. Tested

On 2026-10-14 at commit 5dab095 of branch `examples/library-examples` (the branch that is merged into `main`), on one Ubuntu 24.04 VM with 4 vCPUs (AMD EPYC-Rome): a fresh clone and a fresh venv with the [`deploy/versions.env`](../../deploy/versions.env) pins, in a shell without project environment variables, no testbed service used:

```bash
git clone -b examples/library-examples https://github.com/Zare2001/AgnosticFL.git && cd AgnosticFL
uv venv --python 3.11.17 .venv
uv pip install --python .venv/bin/python "torch==2.14.1+cpu" --index-url https://download.pytorch.org/whl/cpu
uv pip install --python .venv/bin/python numpy==2.4.4 safetensors==0.8.0 pyyaml==6.0.3 pytest -e ./flhub_core -e .
uv pip install --python .venv/bin/python flwr==1.39.0
. .venv/bin/activate
python examples/flower/server/adapter.py --check
python examples/flower/run_local.py
```

Output (excerpt; timestamps and the working directory shortened):

```
health: {"ok": true, "name": "flower-adapter"}
discover: flower flower 1.39.0 flhub/0.2 {'flw-h5': 'unknown', 'flw-h6': 'unknown'}
working directory /tmp/flhub-flower-example-…
relay run started, flw-h5 and flw-h6 online after 32 s
… INFO round 1/3 | 2/2 sites | 7.5s | agg 0.00s eval 0.1s | acc 0.6250
… INFO round 2/3 | 2/2 sites | 7.8s | agg 0.00s eval 0.1s | acc 0.6250
… INFO round 3/3 | 2/2 sites | 6.8s | agg 0.00s eval 0.1s | acc 0.6250
INV-1 full sim vs /tmp/flhub-flower-example-…/runs/flower-local-latest: hashes_equal=True max_abs_diff=0.000e+00 -> PASS
flower example: PASS
```

`run_local.py` started the hub core, a SuperLink, the example adapter and two SuperNodes for the benchmark sites `flw-h5` and `flw-h6`, all on `127.0.0.1` on free ports. The FAB carried the example ServerApp and ClientApp in place of the library's. The orchestrator then trained 3 rounds through Flower (median 7.3 s per round), and `hub.sim` trained the same configuration without a framework. Both ended with the same global model (`sha256:54c4f093…`); test accuracy after 3 rounds is 0.625, the share of the majority class, as in [getting started](../../docs/getting-started.md). Clone, install and both checks took 2 min 6 s with a warm `uv` cache. All processes were stopped and the working directory was removed.

Flower's simulation engine is not used: its simulated nodes get only `partition-id` and `num-partitions` as node config (`flwr/server/superlink/fleet/vce/vce_api.py`) and it needs Ray, while the hub's relay identifies hospitals by the `site-id` of a real SuperNode.

## 5. Links

- [`clients/flower/`](../../clients/flower/): the library's Flower app (ClientApp, record helpers, relay ServerApp)
- [`adapters/flower/`](../../adapters/flower/): the Flower adapter service
- [Flower guide](../../docs/frameworks/flower.md): server side (§2), client side (§3), deployment next to an existing federation (§4)
- [Client contract](../../docs/client-contract.md): what every hospital-side client must do

# NVFlare example

## 1. What this shows

- **Client:** what an NVFlare hospital client must contain to train with the hub: NVFlare's standard Client API loop around one call to `flhub_core.train`, every line marked as NVFlare's pattern or the hub's addition.
- **Server:** how the hub's NVFlare side is built from the library: the *relay job* that runs inside the NVFlare server, and the *adapter* next to it that the orchestrator talks to.
- **Without a deployment:** everything runs on one machine with NVFlare's own simulator, ending with the hub's equivalence check against a framework-free simulation.

The production code is [`clients/nvflare/`](../../clients/nvflare/) and [`adapters/nvflare/`](../../adapters/nvflare/); the full guide is [NVFlare](../../docs/frameworks/nvflare.md).

## 2. Client

| File | What |
|---|---|
| [`client/client.py`](client/client.py) | the minimal NVFlare client of the [client contract](../../docs/client-contract.md); the same steps as [`clients/nvflare/client.py`](../../clients/nvflare/client.py), with comments |

| Client-contract item | Where in `client.py` |
|---|---|
| receive the global weights | `flare.receive()` → `model.params` (`FULL`, numpy) |
| receive `train_config` and the round | `model.meta["train_config"]`, round = `model.current_round` |
| data and data variant | `load_site($FLHUB_DATA_PATH or $FLHUB_DATA_DIR/<site>.csv)`; `train` picks `train_config["data_variant"]` |
| run the shared training | `train(model.params, data, TrainConfig.from_dict(config))` |
| return weights, `num_examples`, metrics (incl. `data_fp`) | `flare.send(FLModel(params_type=FULL, params=weights, metrics=metrics, meta={"num_examples": ...}))` |
| report the code version | `meta["client_code_version"] = flhub_core.code_version()` |
| site identity | `flare.get_site_name()`: the NVFlare client name is the hub's site id |

**NVFlare's standard pattern:** `flare.init`, `flare.get_site_name`, `flare.is_running`, `flare.receive`, `flare.send` with an `FLModel` of `FULL` weights, and the aggregation key `NUM_STEPS_CURRENT_ROUND`. **Added for the hub:** the data path, the configuration taken from `meta` plus the round, the call to `flhub_core.train`, and `num_examples`, the metrics and `client_code_version` in the reply.

**How it runs:** nobody starts the client by hand. NVFlare ships it inside the relay job (`custom/client.py`, next to `custom/flhub_core/`) and runs it in-process with `ScriptRunner`. `server/run_local.py` runs it in NVFlare's simulator for the sites `nvf-h1` and `nvf-h2`; its log (`simulator.log`) shows one line per round and site, for example `[example] nvf-h1: round 1, 785 examples, loss 0.6005`.

## 3. Server

| File | Library parts | What it shows |
|---|---|---|
| [`server/relay_job.py`](server/relay_job.py) | `HubRelayController`, `stage_flhub_core` | the relay job with NVFlare's Job API: the relay workflow on the server (imported from the installed hub package, `set_app_packages`), the example client on every client (`ScriptRunner`, relative path, numpy), `flhub_core` as custom code; `export` writes the job folder, `simulate` runs it in NVFlare's simulator |
| [`server/adapter.py`](server/adapter.py) | `NVFlareAdapter`, `FlareAdmin`, `HubClient` | the adapter for a real NVFlare server: flhub JSON-RPC and the `/relay/*` routes, the FLARE API site probe and the relay-job keeper; `--check` runs it without an NVFlare server |
| [`server/run_local.py`](server/run_local.py) | `RelayAdapter`, `HubClient`, `hub.core`, `hub.orchestrator`, `hub.sim` | the whole path on one machine: hub core, adapter, relay job in the simulator, 3 rounds, equivalence check |

**Structure.** NVFlare's server-side workflow must run *inside* the NVFlare server, so the NVFlare adapter is a `RelayAdapter`, not a `BaseAdapter` ([adding a framework](../../docs/adding-a-framework.md)):

- the **relay** (`HubRelayController`, an NVFlare `ModelController`) long-polls the adapter for hub rounds, sends one NVFlare task per site and posts each reply back as soon as it arrives;
- the **adapter** serves the orchestrator and the relay. The NVFlare-specific part adds a site probe through the FLARE API and a keeper thread that submits, adopts and restarts the relay job. In `run_local.py` the simulator starts the job itself, so a plain `RelayAdapter` is enough there.

**Settings.** The adapter URL and the hub-core URL are written into the job's server configuration (the relay's arguments); the site ids are the NVFlare client names; the adapter logs in with an admin startup kit that may submit jobs with custom code; the adapter listens on `127.0.0.1` (port 8811 on the reference testbed).

**Hub core.** The relay reads the global model from the hub core and stores each hospital's reply there; the adapter and the orchestrator pass only blob references.

**How to run:**

```bash
python examples/nvflare/server/adapter.py --check                       # no NVFlare server needed
python examples/nvflare/server/relay_job.py export --out /tmp/nvf-jobs  # the job folder for a real server
python examples/nvflare/server/run_local.py [--rounds 3] [--keep]       # end to end with NVFlare's simulator
```

`run_local.py` stops everything it started on every exit. After a failure or Ctrl-C it first asks the relay to end the job, the normal way out, and then stops whatever still runs together with its children: NVFlare's simulator runs its server and one worker per client as separate processes in new sessions, so a process-group kill would miss them.

With a real NVFlare server: export the job, then `python examples/nvflare/server/adapter.py --admin-kit <admin kit dir> --job-dir /tmp/nvf-jobs/flhub-relay --sites <client names>`, and list the adapter's URL and sites in the orchestrator's configuration. The steps for an existing federation are in the guide's [§4](../../docs/frameworks/nvflare.md#4-deploying-next-to-an-existing-nvflare-federation).

## 4. Tested

2026-10-14, Ubuntu 24.04 (AMD EPYC-Rome): a fresh clone of branch `examples/library-examples` at `8bb0e64` and a fresh virtual environment with the pins of [`deploy/versions.env`](../../deploy/versions.env); no NVFlare deployment was used.

```bash
. deploy/versions.env
uv venv --python "$PYTHON_VERSION" .venv
uv pip install --python .venv/bin/python "torch==${TORCH_VERSION}+cpu" --index-url "$TORCH_INDEX_URL"
uv pip install --python .venv/bin/python "numpy==$NUMPY_VERSION" "safetensors==$SAFETENSORS_VERSION" \
    "pyyaml==$PYYAML_VERSION" "nvflare==$NVFLARE_VERSION" -e ./flhub_core -e .
. .venv/bin/activate
python examples/nvflare/server/adapter.py --check
python examples/nvflare/server/relay_job.py export --out /tmp/nvf-jobs
python examples/nvflare/server/run_local.py
```

- `adapter.py --check`: `health {'ok': True, 'name': 'nvflare-adapter'}`; `discover` answers `adapter=nvflare framework 2.9.0 protocol flhub/0.2` with `nvf-h1` and `nvf-h2` offline (no relay, no client); the keeper submits no job; `check: PASS`.
- `relay_job.py export`: the folder `flhub-relay/` holds `app/config/config_fed_server.json` (`HubRelayController`), `app/config/config_fed_client.json` (`ClientAPIExecutor`, `in_process`, `task_script_path: client.py`, numpy), `app/custom/client.py` (byte-identical to `client/client.py`), `app/custom/flhub_core/` and `meta.json` (the app on `@ALL`).
- `run_local.py`: `"status": "finished"`, 3 rounds in 9.6 s, median round 2.39 s (NVFlare's task-request interval, [guide §5.3](../../docs/frameworks/nvflare.md#53-runtime-overhead)), final test accuracy 0.625 (still the majority class after 3 rounds, as in stage 1); the simulator exited with 0; `INV-1 full sim vs …/nvflare-example-latest: hashes_equal=True max_abs_diff=0.000e+00 -> PASS`. 77 s in total, including the 4 MB data download and the simulator start. The round-1 training losses, 0.6005 (`nvf-h1`) and 0.6182 (`nvf-h2`), equal those of the conformance run against the reference deployment ([evidence](../../docs/results/evidence/stage1/conformance-nvflare-central.json)).
- Interrupted runs (failure paths), with `--rounds 20` so that every signal arrived mid-run: SIGTERM, SIGHUP and SIGINT to the `run_local.py` process and SIGINT to its process group (what Ctrl-C in a terminal sends), each 5 s after the simulator started, and `timeout 30` (SIGTERM to the process and its group). Exit codes 143, 129, 130, 130 and 124; in every case no process of the run and no work directory was left (`ps`, `ls`), tested on a fresh clone of `cf7eb5a`.

## 5. Links

- Production code: [`clients/nvflare/`](../../clients/nvflare/), [`adapters/nvflare/`](../../adapters/nvflare/)
- Guide: [NVFlare](../../docs/frameworks/nvflare.md); [client contract](../../docs/client-contract.md); [adding a framework](../../docs/adding-a-framework.md)

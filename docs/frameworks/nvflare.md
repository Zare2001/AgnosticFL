# NVFlare guide

This guide covers the NVFlare adapter, the hospital-side NVFlare client, and what it costs to run the framework-agnostic hub with NVFlare. For the protocol, see [`protocol.md`](../protocol.md). For the framework-independent client contract, see [`client-contract.md`](../client-contract.md).

## 1. Summary

| | |
|---|---|
| Tested version | NVFlare **2.9.0** with Python 3.11.17, torch 2.14.1+cpu, numpy 2.4.4, safetensors 0.8.0 ([`deploy/versions.env`](../../deploy/versions.env)) |
| Protocol | `flhub/0.2` (also serves `flhub/0.1` callers) |
| Status | Tested in stage 1 (2 hospitals, `flhub/0.1`) and stage 2 (2 hospitals, plus 2 extra hospitals for the 12-site run). Conformance suite v2 against the reference deployment: 13 of 13 tests pass, and every returned weight equals local training bit for bit (max abs diff 0.0). |
| Hospital code | [`clients/nvflare/client.py`](../../clients/nvflare/client.py), 48 lines, the standard NVFlare Client API loop. 0 lines changed since stage 1. |
| Rollout channel | The job's custom code (`custom/client.py` and `custom/flhub_core/`), deployed by the NVFlare server like any other job. |

The adapter is a small HTTP service next to the NVFlare server. It keeps one long-running NVFlare job, `flhub-relay`, alive through the FLARE API. Inside that job, a server workflow (the *relay*) takes each hub round from the adapter, sends it to the hospitals as one standard NVFlare task per site, and hands each hospital's reply to the hub as soon as it arrives.

Sources for this guide. Full stage reports at their tags: [stage-1 NVFlare section](https://github.com/Zare2001/AgnosticFL/blob/stage1-final/docs/report/nvflare.md), [stage-2 NVFlare section](https://github.com/Zare2001/AgnosticFL/blob/stage2-final/docs/report/stage2/nvflare.md). Decisions: ADR-006, ADR-011, ADR-012, ADR-018 and ADR-019 in [`design/decisions.md`](../design/decisions.md). Evidence: [`results/evidence/stage2/nvflare/`](../results/evidence/stage2/nvflare/), conformance reports [stage 1](../results/evidence/stage1/conformance-nvflare-central.json) and [stage 2](../results/evidence/stage2/conformance-v2-nvflare-central.json).

## 2. Server side: how the adapter maps flhub onto NVFlare

### 2.1 Components

| Component | Code | Runs | Role |
|---|---|---|---|
| NVFlare server | standard startup kit from `nvflare provision` | central host; ports 8002 and 8003 | Hospitals connect to it over mTLS. The adapter uses its admin port. |
| Adapter service | [`adapters/nvflare/service.py`](../../adapters/nvflare/service.py), `NVFlareAdapter(RelayAdapter)` | central host, `127.0.0.1:8811` | Serves flhub JSON-RPC to the orchestrator and the relay routes to the relay. Runs the relay-job *keeper* and a per-round *site watcher*. |
| FLARE API wrapper | [`adapters/nvflare/flare_admin.py`](../../adapters/nvflare/flare_admin.py) | inside the adapter | One reconnecting admin session (operator's admin kit): list, submit, abort and delete jobs; probe which job each client runs. |
| Relay job builder | [`adapters/nvflare/job.py`](../../adapters/nvflare/job.py) | once, at install | Exports the job folder `flhub-relay` with NVFlare's Job API. |
| Relay workflow | [`adapters/nvflare/relay_controller.py`](../../adapters/nvflare/relay_controller.py), `HubRelayController(ModelController)` | in the NVFlare server's job process | Long-polls the adapter, sends one task per site, posts each reply back. |
| Hospital client | [`clients/nvflare/client.py`](../../clients/nvflare/client.py) | each hospital, shipped inside the job | Standard Client API loop around `flhub_core.train` (§3). |
| Native baseline | [`adapters/nvflare/native.py`](../../adapters/nvflare/native.py), [`deploy/central/nvflare/native/run.sh`](../../deploy/central/nvflare/native/run.sh) | central host, for the E7 comparison only | NVFlare's built-in `FedAvg` with recording hooks. Not needed to run the hub. |

The relay job is built with the standard Job API:

```python
job = FedJob(name="flhub-relay", min_clients=1)
job.set_app_packages(["adapters"])            # the relay runs from the installed package
job.to_server(HubRelayController(adapter_url=..., hub_url=...), id="relay")
job.to_clients(ScriptRunner(script="client.py", framework=FrameworkType.NUMPY))
job.to_clients(staging)                        # flhub_core/*.py -> custom/flhub_core/
```

`set_app_packages(["adapters"])` keeps the relay out of the job, so the NVFlare server imports it from its own Python environment. The only custom code in the job is the hospital side: `custom/client.py` and `custom/flhub_core/`. The script path is relative on purpose: with an absolute path, NVFlare makes the clients run a local file at that path instead of the shipped copy (§7).

### 2.2 One round, step by step

1. The orchestrator calls `dispatch` on the adapter with the global model (a hub-core blob reference), the `train_config`, the sites and `deadline_s`.
2. The adapter fails every site that is not online at once (a per-site error, `flhub/0.2`); the other sites go to the relay's queue.
3. The relay receives the job from `GET /relay/next` (long-poll, 20 s), reads the global model from the hub core and builds one `FLModel`:

   ```python
   model = FLModel(params_type=ParamsType.FULL, params=params, current_round=rnd,
                   meta={"train_config": dict(job["config"])})
   ```

4. It sends one NVFlare task per site, with the deadline as the NVFlare task timeout:

   ```python
   task = self._prepare_task(data=model, task_name="train", timeout=timeout, callback=...)
   self.send(task=task, fl_ctx=self.fl_ctx, targets=[site], send_order=SendOrder.ANY)
   ```

   One task per site, instead of one broadcast, gives per-site results and a per-site cancel. It also avoids the deep copy of the model that NVFlare makes for every broadcast task (`apis/impl/wf_comm_server.py:306-315`).
5. NVFlare serializes the numpy arrays with FOBS and streams them out of band through the sender's download service in 2 MiB chunks. The hospital trains and returns full weights.
6. For each reply, the relay checks that the sender (`meta["client_name"]`, the authenticated NVFlare client name) is that site, that the weights are `FULL` and that `num_examples` is positive. It stores the weights in the hub core and posts the site's result (`POST /relay/site_result`), with three timings added to the site's metrics: `nvflare_relay_prep_s`, `nvflare_relay_reply_s` and `nvflare_relay_store_s`. An error reply (NVFlare return code not OK) goes to the hub at once (`POST /relay/site_error`).
7. Every second, the relay asks the adapter which sites the hub still waits for (`GET /relay/job`). It cancels the NVFlare task of every other site (cancelled, failed, past the deadline).
8. The orchestrator collects the results (all of them, or a partial set) and aggregates on the hub.

`ModelController` is created with `persistor_id=""` (the hub owns the global model) and `ignore_result_error=True` (a client error must not end the job).

### 2.3 Sites: identity, online status and the relay job

- **Identity.** The hub site id is the NVFlare client name: the `name` in `project.yml` and the common name of the client certificate. The relay accepts a reply only if NVFlare's authenticated `client_name` matches the site.
- **Online** means both: the relay reports the site as a live client of the relay job, and the server's `get_client_job_status()` probe shows that client running the relay job. The probe is needed because NVFlare keeps a stopped client "connected" for minutes in `get_system_info()` and in the job's disconnect time. A dead client answers the probe with `no_reply` within about 1 s.
- **Keeper** (every 10 s, in the adapter):
  - submits the relay job when none is active, once every base site is connected or 30 s after the first one connected;
  - adopts a relay job that is already running (for example after an adapter restart);
  - restarts the job gracefully (relay shutdown, then a new job) when a connected site has been outside it for 15 s and no round is in flight, because NVFlare does not let a restarted client rejoin a running job;
  - aborts a job whose relay has been silent for 30 s, or that did not end 60 s after a shutdown request;
  - treats job-store entries that survived an abrupt server stop as stale (deletes or ignores them, then submits a new job).
- **Site watcher** (every 5 s during a round): fails a site that has been offline for 30 s; the other sites go on. An unknown probe result never fails a site.
- **Extra sites.** Sites in `--sites` but not in `--base-sites` are served but never delay the job start and never block a round. The 12-site run used this for two extra NVFlare hospitals.

The timing constants are at the top of [`service.py`](../../adapters/nvflare/service.py) (`KEEPER_PERIOD_S`, `SUBMIT_GRACE_S`, `REJOIN_AFTER_S`, `RELAY_DEAD_S`, `SHUTDOWN_GRACE_S`, `OFFLINE_FAIL_S`, `SITE_WATCH_S`).

### 2.4 Ports and configuration

| Port | Bound to | Used by |
|---|---|---|
| 8002 | `0.0.0.0` | hospital clients (mTLS); the only port opened to the hospitals |
| 8003 | `0.0.0.0` (provisioning cannot set the admin host); closed to the outside by the testbed's firewall | adapter (FLARE API) |
| 8811 | `127.0.0.1` | orchestrator (JSON-RPC), relay (HTTP) |
| 8800 | `127.0.0.1` | hub core (model blobs), used by adapter and relay |

The listen address follows the server name in `project.yml`: `localhost` binds `127.0.0.1`, any other name binds `0.0.0.0`. The reference testbed uses the name `flhub-central`, resolved through `/etc/hosts` on every host.

Settings live in [`deploy/topology.env`](../../deploy/topology.env), [`deploy/versions.env`](../../deploy/versions.env) and [`deploy/central/nvflare/common.sh`](../../deploy/central/nvflare/common.sh) (`NVF_*` variables, all overridable). The two programs take:

```bash
python -m adapters.nvflare.job --out <jobs dir> [--adapter-url http://127.0.0.1:8811] [--hub-url http://127.0.0.1:8800] [--min-clients 1]
python -m adapters.nvflare.service --admin-kit <admin kit dir> --job-dir <jobs dir>/flhub-relay \
    [--port 8811] [--sites nvf-h1,nvf-h2,...] [--base-sites nvf-h1,nvf-h2] [--hub-url ...]
```

### 2.5 Start, stop and health (reference testbed)

| Script (`deploy/central/nvflare/`) | What it does |
|---|---|
| `install.sh` | venv with the pinned versions, `flhub_core` and the hub package; provisioning (`provision.sh`); exports the relay job folder |
| `provision.sh [--force]` | `nvflare provision`: server (ports 8002/8003), one client per hospital (org `hospital<N>`), one `project_admin` user for the adapter; packs each hospital kit as `<site>.tgz`. Keeps existing certificates unless `--force`. |
| `provision_extra.sh` | kits for more hospitals with `nvflare provision --add-client`: same root CA, no server restart |
| `start.sh` | starts the server with the kit's `startup/start.sh`, waits for 8002/8003, then starts the adapter; the keeper submits the relay job |
| `status.sh` | server ports, adapter `discover` (one line per site), relay status; exit 0 only if every base site is online |
| `stop.sh` | stops the adapter (it asks the relay to end the job and aborts the job if it does not end), then the server through the kit's `shutdown.fl` |
| `restart_server.sh` | restarts only the NVFlare server; hospitals reconnect and the keeper submits a new relay job |
| `purge.sh` | deletes finished jobs from the server's job store (FLARE API) and stale server job workspaces |

The hospital side has its own scripts in [`deploy/nvflare-vm/`](../../deploy/nvflare-vm/) (`install.sh`, `start.sh`, `stop.sh`, `status.sh`). `GET /relay/status` on the adapter shows the relay's liveness, the relay job id and the job each client runs.

## 3. Client side: what the hospital code must contain

The hospital runs NVFlare's standard client (the kit's `startup/start.sh`). The training code reaches it inside the relay job: NVFlare starts a job process with the `ClientAPIExecutor` (`execution_mode: in_process`, `task_script_path: client.py`, `params_exchange_format: numpy`), which runs `custom/client.py`. The whole script is 48 lines; these are the essential ones.

**Imports.** NVFlare's Client API, plus the shared training library.

```python
import nvflare.client as flare                       # NVFlare standard
from nvflare.client import FLModel, ParamsType       # NVFlare standard

import flhub_core                                    # flhub: shared training code
from flhub_core.data import load_site, site_path     # flhub
from flhub_core.train import TrainConfig, train      # flhub
```

**Start-up and data.** `flare.init()` and `flare.get_site_name()` are NVFlare's standard start of a Client API script. The data file is the hospital's only setting: `$FLHUB_DATA_PATH`, else `$FLHUB_DATA_DIR/<site>.csv` (default directory `~/flhub-data`). `load_site` returns a lazy handle, so data variants resolve per round (below).

```python
flare.init()                                         # NVFlare standard
site = flare.get_site_name()                         # NVFlare standard: the client name = hub site id
path = os.environ.get("FLHUB_DATA_PATH") or str(site_path(site))   # flhub
data = load_site(path)                               # flhub
```

**The round loop.** The receive loop is NVFlare's standard pattern. The flhub parts are where the configuration comes from and the one training call: the hub's `train_config` travels in `FLModel.meta["train_config"]`, and `FLModel.current_round` is the hub round, which seeds the data order.

```python
while flare.is_running():                            # NVFlare standard
    global_model = flare.receive()                   # NVFlare standard
    if global_model is None:  # job finished
        break
    cfg = {"round": global_model.current_round, **global_model.meta.get("train_config", {})}   # flhub
    weights, num_examples, metrics = train(global_model.params, data, TrainConfig.from_dict(cfg))   # flhub
```

**The reply.** Full weights in a standard `FLModel`. `NUM_STEPS_CURRENT_ROUND` is NVFlare's standard aggregation weight key; NVFlare's own `FedAvg` uses it (E7). The flhub fields are `num_examples`, `client_code_version` and the metrics returned by `flhub_core.train`.

```python
flare.send(FLModel(
    params_type=ParamsType.FULL, params=weights,     # NVFlare standard: FULL numpy weights
    metrics=metrics,                                 # flhub: train_loss, train_acc, train_time_s, data_load_s, peak_rss_mb, data_fp
    meta={
        "num_examples": num_examples,                # flhub: FedAvg weight
        "NUM_STEPS_CURRENT_ROUND": num_examples,     # NVFlare's standard aggregation weight key
        "client_code_version": flhub_core.code_version(),   # flhub: content hash of the training code
    },
))
```

The relay rejects a reply that is not `FULL` or has no positive `num_examples` (it falls back to `NUM_STEPS_CURRENT_ROUND`). A missing `client_code_version` is reported as `unknown`, and the hub then cannot verify the code the site ran.

**Data and data variants.** Hospitals hold sha256-verified CSV files (`label,x0..x783`), written by `python -m flhub_core.data materialize`. A data variant is a file `<variant>-<site>.csv` next to `<site>.csv`. The hub selects it per run with `train_config["data_variant"]`, so a new variant needs a new file at the hospital, never a configuration change. Each reply carries `data_fp` (the first 32 bits of the data fingerprint), which the hub checks against the partition manifest.

**Packaging and rollout.** `client.py` and the `.py` files of `flhub_core` travel in the job's `custom/` folder. NVFlare 2.9.0 **appends** `custom/` to the job process's `sys.path` (`utils/job_launcher_utils.py:486-490`), so a `flhub_core` installed in the hospital's Python environment shadows the job's copy. Hence the hospital must not install `flhub_core`. The stage-2 rollout of `flhub_core` 0.2 showed both sides of this:

| Step | Who | What | Wall time | Downtime |
|---|---|---|---|---|
| 1 | hospital | provision the stage-2 data files next to `<site>.csv` (manifest-checked) | 12.9 s | none |
| 2 | operator | export a relay job that carries `custom/flhub_core` (`install.sh`), restart the adapter and server so that the keeper submits it | – | relay-job restart |
| 3 | hospital, once | remove `flhub_core` from the client's Python environment and restart the client ([`switch_s2.sh`](../../deploy/nvflare-vm/switch_s2.sh)) | 82.6 s for both sites | 50.3 s per site |

Before step 3, both hospitals ran the job with their installed `flhub_core` 0.1.0 instead of the shipped 0.2.0. After it, `client_code_version` reported `0.2.0+fe357a1dbec1` at both sites and the selfcheck digest in the hospital runtime matched the hub's. Every later `flhub_core` version reaches the hospitals with the next relay job, with no hospital step. Rollback means submitting an older job. Sources: [hospital ledger](../results/evidence/hospital-ledger.md), ADR-018 in [`design/decisions.md`](../design/decisions.md).

**Hospital-side footprint** (NVFlare rows of the [hospital ledger](../results/evidence/hospital-ledger.md)):

| | What the hospital has or does |
|---|---|
| Code | 0 lines written or changed by the hospital; `client.py` arrives with the job |
| Software | Python environment with `nvflare==2.9.0`, `torch==2.14.1+cpu`, `numpy==2.4.4`, `safetensors==0.8.0`; **no** `flhub_core` |
| Credentials | the NVFlare startup kit from the operator: `fed_client.json` (server `flhub-central:8002`, `connection_security: mtls`), `client.crt`, `client.key`, `rootCA.pem` |
| Configuration | name resolution of the server name (`/etc/hosts`), the data path (`FLHUB_DATA_PATH` or `FLHUB_DATA_DIR`) |
| Data | `<site>.csv` and one file per data variant, sha256-verified |
| Operations | start the client with the kit; once, remove an installed `flhub_core` and restart the client |

### Checklist: your NVFlare client must …

- [ ] use the Client API loop (`flare.init`, `flare.receive`, `flare.send`) and exchange **numpy** parameters as **`FULL`** weights (`ScriptRunner(..., framework=FrameworkType.NUMPY)`, in-process);
- [ ] build the training config as `{"round": model.current_round, **model.meta["train_config"]}` and pass it, with the received parameters, unchanged to `flhub_core.train`;
- [ ] reply with the returned weights, `meta["num_examples"]` (and `NUM_STEPS_CURRENT_ROUND`), `meta["client_code_version"] = flhub_core.code_version()` and the returned metrics;
- [ ] load data with `flhub_core.data.load_site(path)`, so that `data_variant` resolves to `<variant>-<site>.csv`;
- [ ] run under the NVFlare client name that the hub uses as site id;
- [ ] take `flhub_core` from the job: no `flhub_core` in the client's Python environment;
- [ ] leave threads, seeds and dtypes to `flhub_core` (one CPU thread, deterministic algorithms, float32), so that its update is bit-identical to the hub's simulation.

## 4. Deploying next to an existing NVFlare federation

The hub reuses an NVFlare 2.9 project as it is: the server, its provisioning and the hospitals' kits stay unchanged. The steps below are what the reference testbed's scripts do; a different layout (for example the hub on another host than the NVFlare server) has not been tested.

1. **Server environment.** Install the hub package and `flhub_core` into the Python environment that runs the NVFlare server: `uv pip install -e ./flhub_core -e .` from a clone of this repository. The server's job process imports the relay from there.
2. **Admin identity.** The adapter needs an admin startup kit of a user that may submit jobs with custom code and list, abort and delete jobs. The testbed provisions a `project_admin` user for this. Keep the kit on the server host: whoever holds it controls the NVFlare project.
3. **Job slot.** The relay job runs permanently and takes one of the server's job slots (`max_jobs`, 4 in NVFlare's provisioning template). Other jobs can run next to it; the E7 native FedAvg jobs did.
4. **Hub core and adapter.** Start the hub core, export the relay job and start the adapter on the server host:

   ```bash
   python -m adapters.nvflare.job --out ~/flhub-runtime/nvflare/server/jobs --min-clients 1
   python -m adapters.nvflare.service --admin-kit <admin kit dir> \
       --job-dir ~/flhub-runtime/nvflare/server/jobs/flhub-relay --sites <client names>
   ```

   The job builder copies `clients/nvflare/client.py` and `flhub_core/` from the clone, so keep the editable install of step 1. Use the NVFlare client names as site ids in the orchestrator's configuration.
5. **Hospitals.** No new credentials and no new software. Each hospital removes `flhub_core` if it had installed it, restarts its client once, and places its data file at `$FLHUB_DATA_DIR/<site>.csv` or sets `FLHUB_DATA_PATH` in the client's environment. Its Python environment needs torch, numpy and safetensors next to NVFlare; for bit-exact results, the versions of [`deploy/versions.env`](../../deploy/versions.env).
6. **Check.** `discover` must list every site as `online` (see `deploy/central/nvflare/status.sh`). Then run the conformance suite against the adapter: `pytest tests/conformance --adapter http://127.0.0.1:8811`.

**Credentials in use:** the hospitals' kits (mTLS client certificates, unchanged), the operator's admin kit (adapter only) and the root CA from `nvflare provision`. New hospitals get kits from `nvflare provision --add-client` with the same root CA; the running server and the existing kits stay valid ([`provision_extra.sh`](../../deploy/central/nvflare/provision_extra.sh)). The testbed kits expire on 2027-10-08; `provision.sh --force` issues new ones, which must then be redistributed.

**Two NVFlare 2.9 defaults to know.** Kits enforce a class allow-list (`class_list_enforcement_mode: enforce`) that does not contain `ClientAPIExecutor`; jobs with custom code (BYOC) bypass it, and the relay job is such a job. A server app without custom code is held to the list (`ComponentNotAuthorized`), which is why the E7 native job ships its workflow as custom code.

## 5. Cost and efficiency for NVFlare

## 6. Limits, known issues and knobs

## 7. Troubleshooting

## 8. Future work and upstream reports

# NVFlare guide

This guide covers the NVFlare adapter, the hospital-side NVFlare client, and what it costs to run the framework-agnostic hub with NVFlare. For the protocol, see [`protocol.md`](../protocol.md). For the framework-independent client contract, see [`client-contract.md`](../client-contract.md).

## 1. Summary

| | |
|---|---|
| Tested version | NVFlare **2.9.0** with Python 3.11.17, torch 2.14.1+cpu, numpy 2.4.4, safetensors 0.8.0 ([`deploy/versions.env`](../../deploy/versions.env)) |
| Protocol | `flhub/0.2` (also serves `flhub/0.1` callers) |
| Status | Tested in stage 1 (2 hospitals, `flhub/0.1`) and stage 2 (2 hospitals, plus 2 extra hospitals for the 12-site run). Conformance suite v2 against the reference deployment: 13 of 13 tests pass, and every returned weight equals local training bit for bit (max abs diff 0.0). In stage 1, the mixed 6-site final run (2 NVFlare sites) is bit-identical to the framework-free simulation; in stage 2, all 69 real runs passed the equivalence check (INV-1) with a maximum weight difference of 0, including every run with NVFlare sites ([tables.md](../results/evidence/stage2/tables.md)). |
| Hospital code | [`clients/nvflare/client.py`](../../clients/nvflare/client.py), 48 lines (39 code lines), the standard NVFlare Client API loop. 0 lines changed since stage 1. |
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
  - after such a shutdown request, aborts the old job as soon as its relay has been silent for 30 s, or after 60 s at the latest;
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

`GET /relay/status` on the adapter shows the relay's liveness, the relay job id and the job each client runs. The adapter's keeper and site status have unit tests against a fake FLARE API: [`tests/unit/test_nvflare_adapter.py`](../../tests/unit/test_nvflare_adapter.py) (12 tests; skipped where `nvflare` is not installed).

The hospital side of the testbed has its own scripts:

| Script (`deploy/nvflare-vm/`) | What it does |
|---|---|
| `install.sh [kit.tgz ...]` | Python environment (`nvflare`, torch, numpy, safetensors; removes an installed `flhub_core`), sha256-verified data files, the operator's kits (archives deleted after unpacking) |
| `start.sh [site ...]` | starts each site with its kit's `startup/start.sh` and `FLHUB_DATA_PATH` set, waits for "Successfully registered client", enables a per-minute cron watchdog |
| `status.sh [site ...]` | per site: client process alive and connected to the server |
| `stop.sh [site ...]` | stops sites through the kit's `shutdown.fl` and disables their watchdog |
| `switch_s2.sh` | the one-time switch to job-shipped `flhub_core` (§3) |
| `extra.sh install\|start\|stop\|status` | the two extra hospitals of the 12-site run |

The other files in [`deploy/nvflare-vm/`](../../deploy/nvflare-vm/) are experiment tooling and are not needed to run the hub: fault injection (`inject.sh`, `inject_watch.py`), CPU throttling (`straggler.sh`), link shaping (`netem.sh`), monitoring (`sysmon.sh`, `run_follow.py`), the end-state check (`endstate.sh`) and analysis scripts.

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

The relay rejects a reply that is not `FULL` or has no positive `num_examples` (it falls back to `NUM_STEPS_CURRENT_ROUND`). A missing `client_code_version` is reported as `unknown`, and the hub then cannot verify the code the site ran. (On the wire, NVFlare 2.9 carries the metrics as `meta["initial_metrics"]` when parameters are present; the server's `FLModel.metrics` holds them again.)

**Data and data variants.** Hospitals hold sha256-verified CSV files (`label,x0..x783`), written by `python -m flhub_core.data materialize`. A data variant is a file `<variant>-<site>.csv` next to `<site>.csv`. The hub selects it per run with `train_config["data_variant"]`, so a new variant needs a new file at the hospital, never a configuration change. Each reply carries `data_fp` (the first 32 bits of the data fingerprint), which the hub checks against the partition manifest.

**Packaging and rollout.** `client.py` and the `.py` files of `flhub_core` travel in the job's `custom/` folder. NVFlare 2.9.0 **appends** `custom/` to the job process's `sys.path` (`utils/job_launcher_utils.py:486-490`), so a `flhub_core` installed in the hospital's Python environment shadows the job's copy. Hence the hospital must not install `flhub_core`. Stage 2 changed the hospital side in four ways; this is the NVFlare column:

| Hospital-side change in stage 2 | NVFlare | Wall time | Downtime |
|---|---|---|---|
| (a) code rollout of `flhub_core` 0.2 | once: remove `flhub_core` from the client's Python environment and restart the client ([`switch_s2.sh`](../../deploy/nvflare-vm/switch_s2.sh)); the operator deploys a relay job that ships `custom/flhub_core` | 82.6 s for both sites, including a 12 s data check | 50.3 s per site |
| (b) data for the stage-2 data variants | data only: the variant files next to `<site>.csv`, checked against the manifests | 12.9 s | none |
| (c) configuration for data variants | none: `flhub_core` finds `<variant>-<site>.csv` next to the configured file | – | – |
| (d) later code updates | none: they arrive with the next relay job | – | – |

Before the switch, both hospitals ran the job with their installed `flhub_core` 0.1.0 instead of the shipped 0.2.0. After it, `client_code_version` reported `0.2.0+fe357a1dbec1` at both sites and the selfcheck digest in the hospital runtime matched the hub's. Every later `flhub_core` version reaches the hospitals with the next relay job, with no hospital step. Rollback means submitting an older job. Sources: [hospital ledger](../results/evidence/hospital-ledger.md), ADR-018 in [`design/decisions.md`](../design/decisions.md).

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
6. **Check.** `discover` must list every site as `online` (`deploy/central/nvflare/status.sh` does this on the testbed). Then run a few rounds and check each site's `client_code_version` in the round records (`rounds/rNNNN.json`, `updates[].client_code_version`); the orchestrator logs a warning when it differs from the hub's `flhub_core`, and with `strict_code_version: true` in the run configuration it rejects such updates. The conformance suite (`pytest tests/conformance --adapter http://127.0.0.1:8811`) compares every returned update with local training on the same data, so it accepts only the project's benchmark sites (`nvf-h1`, `nvf-h2` with the partitions of `flhub_core.data`): it tests the adapter, for example on test clients provisioned with those names, not the real hospitals.

**Credentials in use:** the hospitals' kits (mTLS client certificates, unchanged), the operator's admin kit (adapter only) and the root CA from `nvflare provision`. New hospitals get kits from `nvflare provision --add-client` with the same root CA; the running server and the existing kits stay valid ([`provision_extra.sh`](../../deploy/central/nvflare/provision_extra.sh)). The testbed kits expire on 2027-10-08; `provision.sh --force` issues new ones, which must then be redistributed. NVFlare 2.9 kits have no `signature.json`, so nothing checks the integrity of a kit's `startup/` folder; the mTLS certificates are the trust anchor.

**Two NVFlare 2.9 defaults to know.** Kits enforce a class allow-list (`class_list_enforcement_mode: enforce`) that does not contain `ClientAPIExecutor`; jobs with custom code (BYOC) bypass it, and the relay job is such a job. A server app without custom code is held to the list (`ComponentNotAuthorized`), which is why the E7 native job ships its workflow as custom code.

## 5. Cost and efficiency for NVFlare

All numbers are from the reference testbed (4 vCPU AMD EPYC-Rome VMs in one data centre, CPU-only, two NVFlare hospitals on one VM). The hub-wide view, with the other frameworks, is in [`cost-and-efficiency.md`](../cost-and-efficiency.md).

### 5.1 At a glance

| Dimension | NVFlare | Source |
|---|---|---|
| Adapter code | 604 code lines in 5 files (`adapters/nvflare/` without `native.py`, which is only for E7) | [loc.md](../results/evidence/loc.md) |
| Hospital code | 39 code lines (`client.py`, 48 lines with comments and blank lines), 0 changed since stage 1 | [loc.md](../results/evidence/loc.md), [ledger](../results/evidence/hospital-ledger.md) |
| Hospital steps for the `flhub_core` 0.2 rollout | once: remove the installed copy and restart the client (50.3 s offline per site) | [ledger](../results/evidence/hospital-ledger.md) |
| Hospital steps per later code version | 0: the next relay job carries it | ADR-018 |
| Hub overhead against NVFlare's own FedAvg (E7) | −0.23 s (−6.5 %) per round with `cnn-small`; +0.10 s (+4.1 %) with a 45 MiB payload | [e7-native-vs-hub.md](../results/evidence/stage2/nvflare/e7-native-vs-hub.md) |
| Fixed cost per round, cost per MiB | a = 2.23 s, b = 0.0212 s/MiB (R² 0.972, 1–512 MiB) | [cost_model.md](../results/evidence/stage2/cost_model.md) |
| Bytes on the wire ÷ payload (E4a) | 1.005–1.064 | [e4_bytes.md](../results/evidence/stage2/e4_bytes.md) |
| Bandwidth efficiency at 20 Mbit/s (E4b) | 0.94 of the lower bound | [cost_model.md](../results/evidence/stage2/cost_model.md) |
| Largest payload carried | 512 MiB per site and direction (15.5 s per round); 1024 MiB on a single-host test | §5.5 |
| Memory at 512 MiB | hospital VM 5.0 GiB (two clients), one client job 2.1 GiB; relay job on the central host 3.7 GiB | §5.5 |
| Overhead per site and round (E3) | 1.6 s (`cnn-small`), 1.9 s (`cnn-medium`), 2.7 s (`resnet18-gn`) | [tables.md](../results/evidence/stage2/tables.md) |
| 6 → 12 sites (E5) | NVFlare fan-out 3.0 s → 2.8 s | [tables.md](../results/evidence/stage2/tables.md) |
| Hospital crash (E6a): detected / recovered | 33.1 s / 74.0 s, no manual action | [e6.md](../results/evidence/stage2/e6.md) |

### 5.2 Implementation and integration cost

Code lines are non-blank, non-comment lines, counted with the rule of [`scripts/docs/loc.py`](../../scripts/docs/loc.py); the component totals are in [loc.md](../results/evidence/loc.md).

| Code | Code lines | Role |
|---|---|---|
| `adapters/nvflare/service.py` | 239 | adapter service, relay-job keeper, site watcher |
| `adapters/nvflare/relay_controller.py` | 208 | relay workflow inside the NVFlare server |
| `adapters/nvflare/flare_admin.py` | 84 | FLARE API session |
| `adapters/nvflare/job.py` | 72 | relay job export |
| `adapters/nvflare/__init__.py` | 1 | |
| **NVFlare adapter** | **604** | on top of `adapters/common` (1182 lines, shared by every adapter) |
| `clients/nvflare/client.py` | 39 | hospital script |
| `adapters/nvflare/native.py` | 240 | E7 baseline only |
| `tests/unit/test_nvflare_adapter.py` | 175 | 12 unit tests of the keeper and site status |
| `deploy/central/nvflare/`, `deploy/nvflare-vm/` | 1137 (+10 configuration) | testbed scripts, including the experiment tooling |

The adapter touches six NVFlare concepts: the server workflow (`ModelController`), the model container (`FLModel`), the Client API, the Job API, the FLARE API and provisioning.

| NVFlare interface | Used for |
|---|---|
| `ModelController` (`send`, `cancel_task`, `get_client_disconnect_time`, `engine.get_clients`, `abort_signal`) and `BaseModelController._prepare_task` | the relay workflow |
| `FLModel`, `ParamsType.FULL`, `FLMetaKey`, `ReturnCode`, `SendOrder` | model exchange and reply checks |
| Client API: `nvflare.client.init`, `receive`, `send`, `is_running`, `get_site_name` | the hospital script |
| Job API: `FedJob`, `to_server`, `to_clients`, `to`, `set_app_packages`, `export_job`, `ScriptRunner` | building the relay job |
| FLARE API: `new_secure_session`, `list_jobs`, `submit_job`, `abort_job`, `delete_job`, `get_client_job_status`; `get_job_meta` for the E7 baseline | the keeper, the online probe and `purge.sh` |
| Provisioning: `nvflare provision`, `--add-client` | kits for the server, hospitals and admin |

Moving the adapter from `flhub/0.1` to `flhub/0.2` (one task per site, per-site results and cancels, keeper changes, the job that ships `flhub_core`) changed `adapters/nvflare/` by +313 −94 lines in four files, plus `native.py` (+284) for the E7 baseline; `deploy/central/nvflare/` by +137 −13 lines; `clients/nvflare/` by 0 lines (`git diff --stat stage1-final stage2-final`).

The hospital-side integration cost is in §3: no code, the standard kit, a data file, and one restart when the rollout channel changes.

### 5.3 Runtime overhead

**Against NVFlare's own FedAvg (E7).** Same two hospitals, same `flhub_core.train`, same configuration; the native path is NVFlare's built-in `FedAvg` workflow, the hub path is the relay. Median over rounds ≥ 2; a round is "model sent → aggregated model" on both paths.

| Configuration | Native round (s) | Hub round (s) | Hub overhead | Max abs weight difference, native vs hub |
|---|---|---|---|---|
| C1: `cnn-small`, `iid`, 20 rounds | 3.50 (IQR 0.50) | 3.27 (IQR 0.50) | −0.23 s (−6.5 %) | 2.98e-08 after round 1, 0.68 after round 20 |
| C2: `payload-45` echo, 5 rounds | 2.52 (IQR 0.38) | 2.62 (IQR 0.27) | +0.10 s (+4.1 %) | 2.2e-08, flat |

Source: [e7-native-vs-hub.md](../results/evidence/stage2/nvflare/e7-native-vs-hub.md); figure [e7_native_vs_hub.png](../results/figures/e7_native_vs_hub.png). The generated results table uses the recorded round duration without the hub's aggregation step for C2 (2.55 s, +1.4 %).

- The hub path costs NVFlare nothing measurable. Both paths sit on the same NVFlare task-request floor (below). The built-in `FedAvg` checks for its results in 0.5 s steps, the relay every 0.1 s, so the C1 rounds were slightly shorter through the hub.
- The weights differ because of the aggregation, not the transport. Native clients train bit-exactly like hub clients. NVFlare's `FedAvg` sums `w_k · n_k` and scales by `1/Σn` in float32, in arrival order; the hub sums `p_k · w_k` in float64 and casts once. With n = 5761 and 5760 the weights are not exact in float32, so the two paths round differently: the one-step difference is 3e-8. Local SGD at learning rate 0.1 then amplifies it: 6.3e-04 after round 2, 0.68 after round 20. The payload echo has no training, so its difference stays at float32 rounding level.
- Accuracy follows the same shape in both C1 runs (the same dips at rounds 3, 5, 9, 13 and 19; rounds 1–2 equal to four decimals). Best round: native 78.1 %, hub 78.4 % (both round 18); mean of the last five rounds: 63.5 % and 66.8 %.

**Where the time goes (E4a, echo models).** Medians over rounds 2–3, NVFlare scope (two hospitals). *Transfer* is NVFlare transport both ways plus the client framework; *hub* is the relay reading the model from the hub core plus storing the replies.

| Payload (MiB) | Round (s) | NVFlare transfer (s) | Hub prep + store (s) | NVFlare VM memory peak (GiB) | Client job RSS peak (GiB) |
|---|---|---|---|---|---|
| 1 | 2.52 | 2.36 | 0.01 | 1.68 | 0.43 |
| 8 | 2.67 | 2.39 | 0.04 | 1.74 | 0.47 |
| 32 | 2.66 | 1.95 | 0.22 | 1.97 | 0.58 |
| 64 | 3.67 | 2.56 | 0.43 | 2.20 | 0.72 |
| 128 | 5.08 | 2.88 | 0.88 | 2.65 | 0.95 |
| 256 | 10.29 | 6.29 | 1.69 | 3.48 | 1.35 |
| 512 | 15.49 | – | – | 5.01 | 2.11 |

Source: [e4a-breakdown.md](../results/evidence/stage2/nvflare/e4a-breakdown.md) (timings), [e4a-memory.md](../results/evidence/stage2/nvflare/e4a-memory.md) (memory); figure [e4a_round_time_vs_payload.png](../results/figures/e4a_round_time_vs_payload.png).

In the mixed scope (six sites, three frameworks) NVFlare's fan-out was 1.8–3.8 s from 1 to 64 MiB and 5.5 s at 128 MiB; vantage6 set those round times ([tables.md](../results/evidence/stage2/tables.md)).

**The ~2.5 s floor.** Below about 64 MiB, an NVFlare round costs 2.3–2.6 s whatever the size. The server tells each client to wait `task_request_interval` (default 2 s, `private/fed/server/server_json_config.py:102`) before its next task request. In the client log of the 1 MiB run, the client side of a round takes 0.55 s, and the next task arrives 2.04 s after the result left. The interval is a server-side job setting (`server.task_request_interval`) that changes latency only; it was left at its default, and the native E7 runs have the same floor.

**Cost model.** Fan-out time per round = a + b·P with P in MiB, fitted on the echo sweep 1–512 MiB: a = 2.23 s, b = 0.0212 s/MiB, R² 0.972. With the slowest site's training time added, it predicts the learning runs within +3 to +5 % (`cnn-small`), 0 to +4 % (`cnn-medium`) and 0 % (`resnet18-gn`). Source: [cost_model.md](../results/evidence/stage2/cost_model.md).

**Overhead per site and round with real training (E3).** Transport, framework and adapter together: 1.6 s (`cnn-small`, 0.2 MiB), 1.9 s (`cnn-medium`, 6.4 MiB), 2.7 s (`resnet18-gn`, 42.6 MiB), the lowest of the three frameworks. As a share of NVFlare's fan-out it falls from 56 % to 1 %. Source: [tables.md](../results/evidence/stage2/tables.md); figure [e3_round_time_breakdown.png](../results/figures/e3_round_time_breakdown.png).

### 5.4 Network efficiency

- **Bytes on the wire** (interface counters of the NVFlare VM ÷ payload moved, E4a): 1.005–1.064 in the NVFlare scope, 1.007 at 512 MiB. The highest ratio is at 1 MiB, where background traffic counts most. One run (8 MiB) reads 0.840: its upload counter is below the payload that demonstrably crossed the link (every round finished with hash-equal weights), so it is a measurement artifact. Source: [e4_bytes.md](../results/evidence/stage2/e4_bytes.md).
- **Bandwidth (E4b)**, `payload-45`, mixed scope, rate and delay emulated with netem in both directions. Lower bound = 4·P/rate (two sites, each P down and P up per round):

| Profile | NVFlare fan-out (s) | Lower bound (s) | Efficiency (bound ÷ measured) |
|---|---|---|---|
| none | 3.3 | – | – |
| 1 Gbit/s, 5 ms | 4.3 | 1.5 | 0.35 |
| 100 Mbit/s, 20 ms | 17.0 | 15.1 | 0.89 |
| 20 Mbit/s, 40 ms | 80.3 | 75.5 | 0.94 |

Source: [cost_model.md](../results/evidence/stage2/cost_model.md); figure [e4b_measured_vs_bound.png](../results/figures/e4b_measured_vs_bound.png). On slow links NVFlare's streamed download comes close to the wire limit; on fast links the fixed floor dominates.

### 5.5 Memory and payload size

- **Hospital VM** (memory in use at peak, two clients and the operating system): 1.68 GiB at 1 MiB up to 5.01 GiB at 512 MiB, 16 % of the VM's 31.3 GiB; one client's job process 0.43 GiB up to 2.11 GiB; the NVFlare client process itself 89–96 MiB (table in §5.3).
- **Central host** (peak RSS, [central-e4a-memory.md](../results/evidence/stage2/central-e4a-memory.md)): the relay's server job process 2.48 GiB at 256 MiB (9.9 × the payload) and 3.69 GiB at 512 MiB (7.4 ×); the adapter 167 MiB and the hub core 47 MiB at 512 MiB. The adapter stays small because the relay reads the global model from the hub core itself (`RelayAdapter.needs_weights = False`). At 45 MiB (E7 C2) the relay job took 585 MiB, the adapter 163 MiB and the hub core 44 MiB ([stage-2 NVFlare section](https://github.com/Zare2001/AgnosticFL/blob/stage2-final/docs/report/stage2/nvflare.md) §5).
- **Largest payload:** 512 MiB per site and direction on the testbed (15.5 s per round, every round hash-equal to the simulation). On a single-host test with NVFlare's default settings: 128 MiB 5–6 s, 256 MiB 9–10 s, 512 MiB 16–17 s and 1024 MiB 32.8 s per round. No NVFlare limit was reached; the stops were the testbed's disk.

### 5.6 Scaling and robustness

**6 → 12 sites (E5).** Two extra NVFlare hospitals with standard kits from the same root CA, no server restart. NVFlare's fan-out stayed flat (3.0 s → 2.8 s): the clients poll the server in parallel, and each trains on half the data (training 0.73 s → 0.36 s, overhead per site 1.9 s → 1.7 s). The extra sites were online 62 s after the request; joining the relay job took a keeper restart of the job. Sources: [tables.md](../results/evidence/stage2/tables.md), `REPORT_STAGE2.md` §6.4 at [tag `stage2-final`](https://github.com/Zare2001/AgnosticFL/blob/stage2-final/REPORT_STAGE2.md) (time to online); figure [e5_scaling.png](../results/figures/e5_scaling.png).

**Faults (E6).** No run needed a manual action, and every run passed its equivalence check with a weight difference of 0: against the simulation with the recorded participants, and for the resumed run against the uninterrupted run.

| Scenario | NVFlare result |
|---|---|
| Hospital killed (client and job process) in round 5, all sites required | site failed after 33.1 s (offline for 30 s), round retried, done 74.0 s after the kill; client process back after 3.0 s, back in a relay job after 55.6 s |
| Same, with `min_sites: 5` | round 5 finished with 5 sites after 34.0 s; the site stayed out for rounds 6–9 and was back 97.2 s after the kill (§6) |
| Straggler at 10 % CPU, `deadline_s` 20 | in time in all 6 rounds: training 8.0 s instead of 0.7 s, round 10.8 s |
| Hub and adapters killed in round 21 | adapter back after 1.1 s; the relay job kept running and re-attached; round 21 redone |
| NVFlare server restarted between rounds | server back after 12.1 s; next round done 84.9 s after the restart, no failed attempt |
| NVFlare server restarted mid-round | round 7 failed after 51.0 s, retried, done 96.1 s after the restart |

Source: [e6.md](../results/evidence/stage2/e6.md); figures [e6_timeline_s2-e6a-minsites.png](../results/figures/e6_timeline_s2-e6a-minsites.png), [e6_timeline_s2-e6e-server-restart.png](../results/figures/e6_timeline_s2-e6e-server-restart.png).

## 6. Limits, known issues and knobs

### 6.1 Known issues

1. **A training-process crash ends the round for every NVFlare site.** When one client's job process exits with an error, NVFlare 2.9.0 fails the whole job (`private/fed/server/fed_server.py:942-951`; no setting). With one relay job for all NVFlare sites, both sites lose the round, and the keeper submits a new job (about 15 s on a single-host test). An exception in `client.py` has the same effect.
2. **A restarted client rejoins only through a relay-job restart.** NVFlare does not readmit a client into a running job. The keeper restarts the job only while no round is in flight, because a restart would kill the other site's round. With all sites required the hub waits for it (back after 56 s in E6a); with `min_sites`, back-to-back rounds keep the site out until the hub pauses (4 rounds, 97 s in E6a).
3. **A relay-job restart briefly takes every NVFlare site offline**, not only the one that needed it (about 15–30 s).
4. **No per-task abort towards a client.** Cancelling a site's task removes it on the server, but the hospital finishes its local training. The late result is dropped, and the site's next task waits until that training ends. This matters for stragglers.
5. **Latency floor of ~2.5 s per round** from `task_request_interval` (§5.3).
6. **Stale job-store entries.** After an abrupt server stop, NVFlare 2.9.0 keeps the jobs that were running as `RUNNING` in its job store (`JobRunner.update_abnormal_finished_jobs()` is never called). Abort answers "not running", delete answers "still running". The keeper ignores such entries; tools that count active jobs must do the same.
7. **Slow failure detection.** A dead client stays "connected" in `get_system_info()` for minutes. The adapter's probe sees it within about 1 s; the site watcher fails the site after 30 s offline (`OFFLINE_FAIL_S`), so a hospital crash costs the round about 33 s (E6a).
8. **Internal API use.** The relay calls the non-public `BaseModelController._prepare_task` and wraps the task's `result_received_cb` to see error replies that `ModelController` drops. Check both when you upgrade NVFlare.

### 6.2 Knobs

Payload-related settings, read from the NVFlare 2.9.0 source. None had to be changed up to 512 MiB.

| Knob | Side | Default | Where |
|---|---|---|---|
| `task_request_interval` | server (job config `server.task_request_interval`) | 2 s | `private/fed/server/server_json_config.py:102` |
| `np_download_chunk_size` (app config; ≤ 0 sends arrays inline) | sender (server for the global model, client for results) | 2 MiB | `app_common/decomposers/numpy_decomposers.py:66`, `fuel/utils/fobs/decomposers/via_downloader.py:551` |
| `streaming_chunk_size` / `streaming_window_size` / `streaming_ack_interval` | both (`comm_config.json`) | 1 MiB / 64 MiB / 16 MiB | `fuel/f3/streaming/stream_const.py` |
| `streaming_max_blob_size` | both (`comm_config.json`) | 4 GiB | `fuel/f3/comm_config.py` |
| `streaming_read_timeout` / `streaming_ack_wait` | both (`comm_config.json`) | 300 s / 300 s | `fuel/f3/streaming/byte_receiver.py`, `byte_streamer.py` |
| `max_message_size` (inline messages only) | both (`comm_config.json`) | 2 GiB − 3 MiB − 16 B | `fuel/f3/comm_config.py`, `fuel/f3/drivers/net_utils.py` |
| `get_task_timeout`, `submit_task_result_timeout`, `communication_timeout` | hospital | none, none, 300 s | `private/fed/client/client_runner.py`, `fed_client_base.py` |
| `memory_gc_rounds` | hospital (job, `ScriptRunner`) | 0 (off) | `job_config/script_runner.py` |
| job store `uri_root` | server kit | `/tmp/nvflare/jobs-storage` | `local/resources.json.default`; `purge.sh` deletes finished jobs |

Adapter-side settings: the NVFlare task timeout is the dispatch's `deadline_s`; the keeper and watcher constants are at the top of [`service.py`](../../adapters/nvflare/service.py) (§2.3).

## 7. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `nvflare provision` exits with `INVALID_ARGS` on `org: hospital-1` | org names must match `^[A-Za-z0-9_]+$` (client names may contain `-`) | use `hospital1` |
| The server listens on `127.0.0.1:8002` only | server name `localhost` binds the loopback interface | provision with a non-loopback server name and resolve it on every host (`/etc/hosts`) |
| Hospitals run a local file instead of the job's `custom/client.py` | `ScriptRunner` keeps an absolute script path, and clients prefer it | build the job with a relative path ([`job.py`](../../adapters/nvflare/job.py) does) |
| The exported job has no `custom/client.py` | the relative path is resolved at export time, and the export ran in another directory | export in the script's directory ([`job.py`](../../adapters/nvflare/job.py) does, and checks) |
| `ClientAPIExecutor` is missing from the kit's class allow-list | NVFlare 2.9 kits enforce an allow-list | nothing to do for jobs with custom code: they bypass the list |
| `ComponentNotAuthorized` when submitting a job | a server app without custom code is held to the allow-list | ship the workflow as custom code |
| A stopped hospital is still listed online | the server keeps dead clients "connected" for minutes | the adapter's `get_client_job_status()` probe (built in) |
| A restarted hospital never trains | NVFlare does not readmit a client into a running job | the keeper restarts the relay job between rounds (built in); back after about 50 s |
| `client_code_version` shows the old `flhub_core` after a new relay job | an installed `flhub_core` shadows the job's `custom/` copy | remove it from the client's environment and restart the client |
| Sites offline for minutes after a server restart; abort says "not running" | the job store kept the old relay job as `RUNNING` | the keeper deletes or ignores stale jobs and submits a new one (built in) |
| `POST /relay/result` returns 404 | the hub had already dropped the task (cancel, deadline) | expected; the late result is ignored |
| The relay job is aborted during a normal stop | the job needs a few seconds to end after the relay returns | `stop.sh` waits up to 20 s before it aborts |
| `startup/stop_fl.sh` waits for a key press | the kit's stop script is interactive | create `shutdown.fl` in the kit directory (`stop.sh` does) |
| Native `FedAvg` trains with defaults from round 2 | `FLModelUtils.update_model` replaces the model meta | re-attach the `train_config` in `send_model()` ([`native.py`](../../adapters/nvflare/native.py)) |
| `native/run.sh` exits with `native: <dir> exists` | it refuses an existing output directory | pass a new directory |
| The client's log shows a warning `posix_spawn … falling back to subprocess` | NVFlare's process launcher | harmless; no action |

## 8. Future work and upstream reports

**What would lift the limits:**

- **One relay job per hospital.** Isolates sites: a crash or restart at one hospital no longer touches the others (issues 1–3), at the cost of one server job process per site.
- **Rejoin without a job restart.** A client that re-attaches to a running job would remove the keeper's restart and its idle-time condition. This needs NVFlare support.
- **Lower `task_request_interval`** for latency-bound use with small models (§5.3).
- **A rollout without any hospital step.** `ScriptRunner(..., launch_external_process=True)` runs the script as an external process with its own directory first on `sys.path`, so the job's `flhub_core` would win even over an installed copy. It was not used because it adds a pipe hop to every model transfer and changes the stage-1 execution mode (ADR-018).
- **Hardening:** bind the admin port to loopback (`admin_host` in `fed_server.json`); give the custom-code permission only to a dedicated submitter role; ship the hospital code as a signed package instead of job custom code.

**Draft upstream issues (not filed).**

> **One client's job-process failure fails the whole job for all clients (NVFlare 2.9.0)**
>
> When the job process of one client dies (killed, or crashed), the server fails the whole job at once: `private/fed/server/fed_server.py:942-951` logs "Failing job … due to reported failure from <client>: exception" and calls `job_runner.fail_run()`. Every other client loses its in-flight task. An unhandled exception in a Client API script also ends the job.
> Reproduce: a job with two clients running a Client API script with `ScriptRunner` (in-process); while both train, `kill -9` the job process of one client. Expected: the controller sees a failed task for that client and decides what to do, as with `ignore_result_error`. Actual: the job fails for both clients 0.1 s after the kill.
> Proposal: a job-level option that reports a client's job-process failure to the controller as a failed task of that client, instead of failing the job.

> **No way to abort a task at the client (NVFlare 2.9.0)**
>
> `Controller.cancel_task()` ends a task on the server, but the client is not told: a Client API script keeps training and sends a result that the server drops. The client's next task waits until that training ends.
> Reproduce: send a long task to one client with `send()`, cancel it after 1 s with `cancel_task()`; the client log shows the training running to its end.
> Proposal: propagate the cancel to the client, so that `flare.is_running()` (or an abort signal in the Client API) ends the current task.

Related observations, not drafted: a restarted client cannot rejoin a running job; jobs that were running at an abrupt server stop stay `RUNNING` in the job store; job `custom/` code is appended to `sys.path`, so installed packages shadow it.

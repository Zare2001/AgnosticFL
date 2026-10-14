# NVFlare agent report (role nvflare, VM 145.38.189.142)

## 1. Scope and ownership

| Area | What it covers |
|---|---|
| NVFlare adapter on the central VM | `adapters/nvflare/` (adapter service, relay controller, relay job builder, FLARE API wrapper); scripts `deploy/central/nvflare/` |
| NVFlare hospitals `nvf-h1` (partition 0) and `nvf-h2` (partition 1) | `clients/nvflare/client.py`; scripts `deploy/nvflare-vm/` |
| Tests | `tests/unit/test_nvflare_adapter.py` |
| Decision record | ADR-006 |

## 2. What I built (ADR-006)

| Component | File | Role |
|---|---|---|
| Hospital client | `clients/nvflare/client.py` (48 lines) | Standard Client API loop: `flare.init()`, then `flare.receive()`, `flhub_core.train`, `flare.send(FLModel(FULL, …))` |
| Relay controller | `adapters/nvflare/relay_controller.py` | `HubRelayController(ModelController)` inside the NVFlare server job. It loops over `adapters.common.RelayClient`: `next_job()`, then `get_model()`, then `send_model_and_wait()`, then `result()` + `post_result()`. |
| Relay job | `adapters/nvflare/job.py` | Job API: `FedJob("flhub-relay", min_clients=2)` with the relay on the server and `ScriptRunner("client.py", framework=NUMPY)` on `@ALL` |
| Adapter service | `adapters/nvflare/service.py` | `NVFlareAdapter(RelayAdapter)` on 127.0.0.1:8811, plus a relay-job keeper that uses the FLARE API and the admin kit |
| FLARE API wrapper | `adapters/nvflare/flare_admin.py` | Thread-safe session that reconnects; methods `active_jobs`, `submit_job`, `abort_job`, `client_jobs` |

Design choices:

- **One long-running NVFlare job.** Each hub round is one `send_model_and_wait(task_name="train", targets=sites, timeout=deadline_s)`.
  - `FLModel.current_round` carries the hub round.
  - `FLModel.meta["train_config"]` carries the dispatch `train_config` plus `round`. The client hands it unchanged to `flhub_core.train`.
- **FULL weights, numpy exchange.** NVFlare converts nothing. Replies carry `meta["num_examples"]` and also NVFlare's standard `NUM_STEPS_CURRENT_ROUND`.
- **The relay is not shipped in the job.** `set_app_packages(["adapters"])` keeps it out; the server imports it from the central venv. The only custom code in the job is `custom/client.py`.
- **Online status.** A site is online only when both hold:
  - the relay reports it as a live job client;
  - the server's `get_client_job_status()` probe shows the client running the relay job.
- **Keeper.** It submits or adopts the relay job. When a connected site has been outside the job for 15 s and no round is running, it restarts the job gracefully.
- **Fail fast.** A round on an offline site fails at once instead of waiting for the deadline.

## 3. Framework specifics (NVFlare 2.9.0, Python 3.11.17)

APIs used:

- `ModelController`: `run`, `send_model_and_wait`, `cancel_all_tasks`, `get_client_disconnect_time`, `engine.get_clients`, `abort_signal`; constructor args `persistor_id=""`, `ignore_result_error=True`.
- `FLModel` and `ParamsType.FULL`. Each reply carries `meta["client_name"]`, the client's authenticated name.
- Client API: `nvflare.client.init/receive/send/is_running/get_site_name`.
- Job API: `FedJob`, `to_server`, `to_clients`, `set_app_packages`, `export_job`, `ScriptRunner`. The client side runs through `ClientAPIExecutor`, `execution_mode=in_process`.
- FLARE API: `new_secure_session`, `submit_job`, `list_jobs`, `get_job_meta`, `abort_job`, `get_client_job_status`.
- Provisioning: `nvflare provision -p project.yml -w ws --force` with server props `fed_learn_port: 8002` and `admin_port: 8003`. The default `connection_security` is mTLS.

Surprises:

1. **`ScriptRunner(script=<absolute path>)`** stores that absolute path in `config_fed_client.json`. Clients then run the local file at that path instead of the job's `custom/` copy. Fix: build the job with a relative path.
2. **2.9 client kits enforce a class allow-list** (`class_list_enforcement_mode: enforce`), and `ClientAPIExecutor` is not on it. Jobs with custom code (BYOC, allowed for `project_admin`) bypass the list.
3. **The listen address follows the server name.**
   - `localhost` binds 127.0.0.1.
   - `flhub-central` binds 0.0.0.0 (`fed_server.py: _is_loopback_host`).
   - Provisioning cannot set `admin_host`, so admin port 8003 also binds 0.0.0.0. The cloud firewall closes it (timeout from this VM).
4. **Dead clients stay "connected" for minutes** in `get_system_info()` and in the job's `get_client_disconnect_time()`. `get_client_job_status()` answers `no_reply` within ~1 s.
5. **A restarted client does not rejoin a running job.**
6. **`startup/stop_fl.sh` is interactive** (`read -n1`). Our scripts touch `shutdown.fl` instead, which is the kit's own mechanism.
7. **Org names** must match `^[A-Za-z0-9_]+$`. Client names may contain `-`.
8. **2.9 kits have no `signature.json`**, so there is no content-integrity check of `startup/`. The mTLS certificates are the trust anchor: root CA `CN=flhub`, valid until 2027-10-08.
9. **Metrics travel as `meta["initial_metrics"]`** when params are also present. On the server they come back as `FLModel.metrics`.

## 4. Hospital-side footprint

**Install** (`deploy/nvflare-vm/install.sh`, one venv). The script runs:

```bash
uv venv --python 3.11.17 ~/venvs/nvflare
uv pip install --python ~/venvs/nvflare/bin/python "torch==2.14.1+cpu" --index-url https://download.pytorch.org/whl/cpu
uv pip install --python ~/venvs/nvflare/bin/python nvflare==2.9.0 numpy==2.4.4 safetensors==0.8.0 -e ./flhub_core
python -m flhub_core.data materialize --sites nvf-h1 nvf-h2 --out ~/flhub-data   # sha256-verified CSVs
```

**Configuration a hospital sets.** These are the only hub-specific items:

| Item | Where | What |
|---|---|---|
| Connection setting | startup kit `startup/fed_client.json` from the operator | server `flhub-central:8002`, `connection_security: mtls`, `fqsn: nvf-h1` |
| Credentials | same kit | `client.crt` (CN=nvf-h1, O=hospital1), `client.key`, `rootCA.pem` |
| Name resolution | `/etc/hosts` | `145.38.205.58 flhub-central` |
| Data path | env `FLHUB_DATA_PATH` | `~/flhub-data/<site>.csv`, set by `start.sh` |

**What runs there and how it arrives.**

- The kit's own `startup/start.sh` starts NVFlare's `client_train`.
- For the relay job, NVFlare starts its `worker_process` with `ClientAPIExecutor` (in_process, `task_script_path: client.py`). `client.py` arrives inside the job from the operator, the standard NVFlare job deployment; nothing is installed at the hospital for it.
- `client.py` imports only `nvflare.client` and `flhub_core`.

**Evidence** (this VM, 13:10Z):

- `uv pip list` on the hospital venv shows `nvflare 2.9.0`, `torch 2.14.1+cpu`, `numpy 2.4.4`, `safetensors 0.8.0`, `flhub-core 0.1.0`, plus their dependencies.
- The hub package is not installed: outside the repo, `import adapters` and `import hub` raise `ModuleNotFoundError`.
- The deployed `app_nvf-h1/config/config_fed_client.json` names only `nvflare.app_common.executors.client_api_executor.ClientAPIExecutor` with `{"execution_mode": "in_process", "task_script_path": "client.py", "params_exchange_format": "numpy"}`.
- `custom/` holds only `client.py`, byte-identical to `b656577:clients/nvflare/client.py`.

**Reproduce on this VM** (after central ran `deploy/central/nvflare/install.sh && start.sh`):

1. `bash deploy/nvflare-vm/install.sh` creates the venv and the data.
2. Central sends the kits encrypted:
   - central: `tools/bus put ~/flhub-runtime/nvflare/server/kits/nvf-h1.tgz --to nvflare --secret` (same for nvf-h2)
   - this VM: `tools/bus get <id> -o ~/flhub-runtime/nvflare/incoming/nvf-h1.tgz` (same for nvf-h2)
3. `bash deploy/nvflare-vm/install.sh` again. This unpacks the kits to `~/flhub-runtime/nvflare/sites/<site>` (mode 700) and deletes the archives.
4. `bash deploy/nvflare-vm/start.sh`. It runs each kit's `startup/start.sh` with the venv on `PATH` and waits for "Successfully registered client". It also enables a per-minute cron watchdog that `stop.sh` disables.
5. `bash deploy/nvflare-vm/status.sh`. Stop with `bash deploy/nvflare-vm/stop.sh` (`shutdown.fl`).

**Final `status.sh` output (DoD 1/2):**

```
nvf-h1: up (pid 81439, connected to flhub-central:8002 = 145.38.205.58, job processes: 1, data /home/zpalanciya/flhub-data/nvf-h1.csv)
nvf-h2: up (pid 81441, connected to flhub-central:8002 = 145.38.205.58, job processes: 1, data /home/zpalanciya/flhub-data/nvf-h2.csv)
status: HEALTHY
```

## 5. Timeline (UTC, 2026-10-13)

| Time | Event | Evidence |
|---|---|---|
| 11:59 | Agent up; 7700 open, 8002 refused (nothing listening yet) | bus #3 |
| 12:05–12:16 | NVFlare 2.9.0 venv; local mTLS prototype; selfcheck `sha256:7c80a5a3…` = central (12:11); real relay job with 2 rounds at diff 0.0 vs `flhub_core.train` | worklog, bus #12 |
| 12:17–12:18 | Client script, relay, job builder, provisioning, hospital scripts | f2c3956, 13a1650, 8c4ef99 |
| 12:30 | Relay on `adapters.common.RelayClient`; adapter service + keeper; central scripts | 97cc459, 4c697bf, b4527fe |
| 12:31 | **M1-nvflare**: island conformance 7/7; island 3-round run == `hub.sim` | bus #24, 957601e, b19f888 |
| 12:32–12:34 | deploy-request; unit tests; deploy-result PASS; kits received encrypted | bus #25, e586fc4, bus #27–#29 |
| 12:36 | Hospitals switched to `flhub-central:8002` | bus #33, 870de71 |
| 12:38 | **M2-nvflare** (central): conformance 7/7, `nvflare-p2` == sim | bus #36 |
| 12:40 | ADR-006 | 9c5efb3 |
| 12:51–12:52 | M2-mixed (central); client watchdog; P3 freeze `b656577` confirmed | bus #55, c11823e, bus #60 |
| 12:55 / 13:07 | Final runs `nvflare-only` / `mixed-6sites` served | W&B pavr8q6u / iehjnc44 |
| 13:09 | **M3-final** (central): mixed-6sites == reference, diff 0.0 in 31 checkpoints | bus #66 |

## 6. Problems and fixes (including dead ends)

| Problem | Component | Symptom | Root cause | Fix | Commit | Time lost |
|---|---|---|---|---|---|---|
| Org name rejected | provisioning | `nvflare provision` exits 4 (INVALID_ARGS) on `org: hospital-1` | org regex `^[A-Za-z0-9_]+$` | orgs `hospital1`, `hospital2` | 8c4ef99 | 2 min |
| Wrong client code path | job builder | `task_script_path` = absolute path on the build machine | `ScriptRunner` keeps absolute paths, and the in-process runner prefers them | build with a relative path (custom/client.py) | 13a1650 | 10 min |
| Island server on loopback only | provisioning | server listened on 127.0.0.1:8002 | server name `localhost` → loopback bind | island only; central uses `flhub-central` (0.0.0.0) | – | 5 min |
| Client class allow-list | hospital kit | `ClientAPIExecutor` missing from the kit's allow-list | 2.9 enforce mode | none needed: BYOC jobs bypass the list (verified in source and in runs) | – | 5 min |
| Own relay HTTP protocol (dead end) | relay/adapter | duplicated `adapters.common` RelayAdapter/RelayClient, which arrived with M0-core | relay written before M0-core | ported to RelayClient | 97cc459 | 20 min |
| Stopped hospital shown online | adapter site status | discover still said online 90 s after the client stopped | long server heartbeat timeout; job disconnect time not set | add the `get_client_job_status` probe | 4c697bf | 15 min |
| Restarted hospital never trains | adapter keeper | site stays offline after a client restart | clients do not rejoin running jobs | graceful relay-job restart; back online in ~50 s | 4c697bf | 15 min |
| Relay job aborted on stop | adapter shutdown | job still RUNNING 3 s after the relay exited, so it was aborted | job needs seconds to finish | wait ≤ 20 s for the job to end, then abort | 4c697bf | 5 min |
| Late result after cancel | relay | `POST /relay/result` returns 404 | hub already dropped the task | watcher calls `cancel_all_tasks()`; 404 tolerated | 97cc459 | 0 (by design) |
| Silent `start.sh` exit | hospital scripts | rc=1 with no output on a fresh kit | `cat log.txt \| wc -l` under `pipefail` | count lines only if the file exists; awk registration check | 870de71 | 5 min |
| Watchdog lock held forever | hospital scripts | (caught in a scratch test) cron runs would always skip | started clients inherited the flock fd | `9>&-` for the client | c11823e | 5 min |
| `pkill -f` killed my shell | operations | exit 144 during the island stop | pattern matched the calling command line | match NVFlare process names + workspace path only | – | 1 min |

## 7. Tests and evidence

- **Unit tests**: `tests/unit/test_nvflare_adapter.py`, 7 tests with a fake FLARE admin, all passing. They are skipped where nvflare is not installed.
- **Conformance, island**: 7 passed (`docs/report/evidence/conformance-nvflare-island.json`).
- **Conformance, central** (run by central): 7 passed (`docs/report/evidence/conformance-nvflare-central.json`).
- **Conformance details, both runs**: max_abs_diff 0.0 in rounds 1, 2 (after busy) and 4 (after cancel); n = 785/785; code `0.1.0+9c6b064c1e96`.
- **Island 3-round run** vs `hub.sim`: diff 0.0 in rounds 0–3 (`docs/report/evidence/equivalence-nvflare-island.json`). Central's `nvflare-p2` gave the same result.
- **Final runs** (tag `final`, `b656577`):
  - `nvflare-only` (pavr8q6u): 30 rounds in 80 s; acc 0.8173, AUC 0.9224, loss 0.4188. It trains on 2 of the 6 partitions (1,570 examples), hence below the 6-site 0.8654.
  - `mixed-6sites` (iehjnc44): 30/30 rounds, 6/6 sites; acc 0.8654, AUC 0.9247; identical to `sim-fedavg-reference` (`equivalence-final.json`).
- **Client logs**:
  - 70 training rounds per site (conformance, nvflare-p2, mixed-p2, nvflare-only, mixed-6sites).
  - 0 warnings and 0 errors in the job logs; the only parent-log warning is NVFlare's benign `posix_spawn … falling back to subprocess`.
  - One relay job (9e4f07a2) served every P2 and P3 run without a restart.
- **Resilience**, tested on the island:
  - server restart: healthy again after 20 s;
  - client stop: offline in ≤ 5 s;
  - client restart: online again after ~50 s;
  - cancel mid-round: the next round is exact.

## 8. Open issues and recommendations

- **Hardening.**
  - Bind admin to loopback (`admin_host: 127.0.0.1` in `fed_server.json`, possible because there is no `signature.json`).
  - Give BYOC only to a dedicated submitter role.
  - Ship `client.py` as a signed package instead of job custom code.
- **Restart cost.** A relay-job restart (after a hospital restart) takes both NVFlare sites offline for ~50 s. One job per hospital, or a future NVFlare client re-attach, would avoid it.
- **Certificates and the admin kit.** Kits expire on 2027-10-08; `provision.sh --force` issues new ones, which must be redistributed. The admin kit stays on the central VM; anyone holding it controls the NVFlare project.

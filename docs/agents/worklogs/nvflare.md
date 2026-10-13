# Worklog — nvflare agent

Newest entries at the bottom. Format: `### <UTC timestamp> — <phase> — <title>` + 2–6 bullets (what, result, evidence, next).

### 2026-10-13T12:15Z — P0 — bootstrap + NVFlare 2.9.0 API study
- VM 145.38.189.142: 4 vCPU EPYC-Rome, 31 GiB RAM, 19G disk. Ports: 7700 OK; 8002 refused (no listener yet). Hooks verified in /tmp (trailer stripped, 87-char subject blocked, wrong-author push blocked).
- `~/venvs/nvflare`: Python 3.11.17 + nvflare 2.9.0 (latest stable 2.x on PyPI, 2026-09-04; proposed to central as pin, bus #5).
- API summary (installed 2.9.0 source):
  - `ModelController(persistor_id=..., ignore_result_error=None|False|True)`; abstract `run()`; `self.abort_signal` set in `control_flow()`.
  - `send_model_and_wait(task_name="train", data: FLModel, targets: list[str]|None, min_responses=None, timeout=0) -> list[FLModel]`; each result has `meta["client_name"]` (authenticated NVFlare client name). `send_model(..., callback)` is the non-blocking variant. `cancel_all_tasks()` unblocks a waiting send from another thread. `sample_clients(n)` -> names of job clients.
  - `FLModel(params_type=FULL|DIFF, params, metrics, current_round, total_rounds, meta)`; to/from Shareable keeps `meta`; `metrics` travel as `meta["initial_metrics"]` when params are present. NVFlare-standard weight key: `FLMetaKey.NUM_STEPS_CURRENT_ROUND`.
  - Client API: `flare.init()`, `flare.receive(timeout=None)`, `flare.send(FLModel)`, `flare.is_running()`, `flare.get_site_name()`.
  - Job API: `FedJob(name, min_clients)`, `job.to_server(ctrl)`, `job.to_clients(ScriptRunner(script, framework=FrameworkType.NUMPY))` (in_process default, `ClientAPIExecutor` underneath; NUMPY exchange = no conversion), `job.set_app_packages([...])` keeps installed packages out of `custom/`, `job.export_job(dir)`.
  - Provisioning: `nvflare provision -p project.yml -w <ws> --force`; server props `fed_learn_port`, `admin_port` (defaults to fed port), `default_host`, `host_names` (SANs), `connection_security` clear|tls|mtls (default mtls). Kits in `<ws>/<project>/prod_NN/<participant>/startup/{start.sh,sub_start.sh,stop_fl.sh,...}`; start.sh runs `python3` from PATH in background. Default authz: project_admin = any (incl. byoc).
  - FLARE API: `new_secure_session(username, startup_kit_location, timeout=10)`, `submit_job(dir)->job_id`, `list_jobs()`, `get_job_status(id)`, `abort_job(id)`, `get_system_info()` -> `client_info[ClientInfo(name,last_connect_time)]`, `job_info`; `get_client_job_status()`. No auto-reconnect: recreate session on SessionExpired/SessionClosed.
- Next: local prototype (provision `localhost`, server + 2 clients, toy relay job) while waiting for M0-core.

### 2026-10-13T12:25Z — P1 — relay + client verified on a local NVFlare network
- `~/venvs/nvflare` now has the pins of deploy/versions.env (torch 2.14.1+cpu, numpy 2.4.4) + flhub_core; selfcheck result sha256:7c80a5a3777915ff… = central (bus #12). Partitions nvf-h1/nvf-h2 materialized (sha256 match).
- Provisioned locally with `deploy/central/nvflare/provision.sh` (NVF_SERVER_NAME=localhost; mTLS), installed kits with `deploy/nvflare-vm/install.sh`, started/stopped clients with `deploy/nvflare-vm/{start,stop,status}.sh` (all idempotent).
- Relay job built by `python -m adapters.nvflare.job` (FedJob: server `adapters.nvflare.relay_controller.HubRelayController` from the installed package; clients ScriptRunner in-process, NUMPY). 2 rounds driven by a throwaway fake adapter: both sites returned weights with max abs diff 0.0 vs direct `flhub_core.train`, tensor order kept, code_version reported; round time 3.6 s (first) / 1.2 s; job ended FINISHED:COMPLETED on `shutdown`.
- Findings: (1) ScriptRunner with an ABSOLUTE script path makes clients run that local path instead of the shipped custom/ copy -> job.py builds with a relative path (custom/client.py). (2) Provisioned client kits enforce a class allow-list, but jobs with custom code (BYOC, allowed for project_admin) bypass it. (3) Server name `localhost` binds 127.0.0.1; a non-loopback name such as `flhub-central` binds 0.0.0.0 (fed_server.py `_is_loopback_host`), so central works although flhub-central resolves to 127.0.0.1 there. (4) Org names must match `^[A-Za-z0-9_]+$`.
- Next: adapt the relay to adapters/common RelayClient/RelayAdapter (M0-core), write adapters/nvflare/service.py, island with hub core + orchestrator.

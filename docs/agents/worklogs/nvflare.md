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

### 2026-10-13T12:31Z — P1 — adapter on adapters/common; island conformance 7/7
- Relay ported to `adapters.common.RelayClient` (next_job/get_model/result/post_result/post_error/report_sites); `adapters/nvflare/service.py` = `NVFlareAdapter(RelayAdapter)` + relay-job keeper (FLARE API, admin kit). Central-side scripts `deploy/central/nvflare/{install,start,stop,status}.sh` (NVF_SERVER_NAME=localhost for the island).
- Island (this VM): hub core :8800, NVFlare server (localhost, mTLS), adapter :8811, clients nvf-h1/nvf-h2. `pytest tests/conformance --adapter http://127.0.0.1:8811 --sites nvf-h1 nvf-h2` -> 7 passed, max_abs_diff 0.0 for rounds 1, 2 and 4 (after cancel); report /tmp/conformance-nvflare.json.
- Cancel: the relay watches `/relay/status`; when its job is no longer pending it calls `cancel_all_tasks()`; the 404 on the late `post_error` is tolerated; the next round is exact.
- Problem solved: after a hospital client stops, the server keeps it as connected (get_system_info, get_client_disconnect_time) for minutes. Fix: site status uses the FLARE API client probe `get_client_job_status()` (dead client = `no_reply`, ~1 s) AND the relay's view; `run_round` fails fast if a site is offline.
- Problem solved: a restarted hospital client does not rejoin the running relay job. The keeper sees a connected site outside the job for >= 15 s, asks the relay to shut down gracefully (no round in flight) and submits a new job; verified: offline 5 s after stop, both online again ~50 s after start. Server restart: clients reconnect by themselves, new job, healthy after 20 s.
- Next: 3-round island run through the orchestrator (waiting for M1-hub), then deploy-request.

### 2026-10-13T12:33Z — P1 — M1-nvflare: island 3-round run equivalent to hub.sim
- `~/venvs/hub` (island only: orchestrator/sim, wandb 0.30.0 offline) on this VM; config `deploy/central/nvflare/island-3r.yaml` (adapter nvflare :8811, sites nvf-h1/nvf-h2, 3 rounds).
- `python -m hub.orchestrator run --config deploy/central/nvflare/island-3r.yaml --wandb offline` -> finished, 3 rounds x 2 sites, 3-4 s per round, test acc 0.625 (run dir ~/flhub-runtime/runs/nvflare-island-3r-20261013T123046Z).
- `python -m hub.sim` with the same config + `scripts/equivalence_check.py` -> max abs weight diff 0.000e+00 in rounds 0-3, acc diff 0.0 pp: EQUIVALENCE PASS.
- P1 criteria met: conformance 7/7, 3-round island run, idempotent scripts (central: install/start/stop/status, VM: install/start/stop/status). Next: deploy-request to central.

### 2026-10-13T12:39Z — P2 — hospitals switched to central; M2-nvflare (central)
- deploy-request b19f888 (bus #25) -> deploy-result PASS (bus #29): central ran deploy/central/nvflare/install.sh + start.sh at e586fc4; server flhub-central (0.0.0.0:8002, admin 8003 closed in the cloud firewall), adapter 127.0.0.1:8811, relay job 9e4f07a2. Kits arrived encrypted (bus files #1, #2).
- Switch on this VM: island server/adapter/hub core stopped; `rm -rf ~/venvs/nvflare && deploy/nvflare-vm/install.sh` -> hospital venv has only nvflare 2.9.0, torch 2.14.1+cpu, numpy 2.4.4, safetensors 0.8.0, flhub_core; kits -> ~/flhub-runtime/nvflare/sites/<site>, archives deleted; `deploy/nvflare-vm/start.sh` -> both registered; status.sh: connected to flhub-central:8002 = 145.38.205.58, 1 job process each. 8003 not reachable from here (good).
- Bug fixed on the way (870de71): start.sh exited silently on a fresh kit (`cat log.txt | wc -l` under pipefail); registration check now one awk (no SIGPIPE false negatives).
- M2-nvflare (central, bus #36): conformance 7/7 vs central :8811 (diff 0.0 rounds 1, 2, 4); `hub.orchestrator --only nvflare --rounds 3 --name nvflare-p2` == hub.sim (diff 0.0).
- Island leftovers removed (island provisioning incl. its keys, prototype, island hub core, offline wandb dirs). `~/venvs/hub` (island-only) still present; remove before DONE.
- Next: keep clients up for mixed + final runs; watch logs; report section docs/report/nvflare.md.

### 2026-10-13T13:10Z — P3 — freeze confirmed, final runs served, M3-final (central)
- Watchdog: deploy/nvflare-vm/start.sh installs a per-minute cron re-run (flock against overlap; the client must not inherit the lock fd -> `9>&-`), stop.sh removes it (c11823e). Tested with a dummy kit in a scratch dir, real clients untouched.
- P3 freeze b656577 confirmed (bus #60): no flhub_core/clients/nvflare diff; the job copy app_<site>/custom/client.py running at both hospitals is byte-identical to b656577:clients/nvflare/client.py; code_version 0.1.0+9c6b064c1e96; status HEALTHY.
- Final runs (central, bus #66): nvflare-only pavr8q6u acc 0.8173 / AUC 0.9224 (30 rounds, 80 s); mixed-6sites iehjnc44 acc 0.8654 / AUC 0.9247, 30/30 rounds, 6/6 sites, == sim-fedavg-reference (diff 0.0, rounds 0-30).
- Client logs: 70 rounds per site in P2+P3, 0 warnings/errors in job logs; relay job 9e4f07a2 served all runs without restart. Island evidence pushed (811cd39).
- Next: on final-call push docs/report/nvflare.md + report-ready; before DONE remove ~/venvs/hub (island-only).

### 2026-10-13T13:18Z — P4 — all-done: nvflare DoD items verified, DONE
- all-done received (bus #84, REPORT.md 1b66a28). Report section docs/report/nvflare.md pushed (873781e, report-ready bus #75); REPORT.md §5/§9/§12 statements on NVFlare checked against it and the evidence: accurate.
- DoD 1: deploy/nvflare-vm/status.sh -> "status: HEALTHY" (exit 0); central's deploy/central/nvflare/status.sh healthy in REPORT.md §12.
- DoD 2: hospital venv = nvflare 2.9.0, torch 2.14.1+cpu, numpy 2.4.4, safetensors 0.8.0, flhub_core (hub package not importable); hub-specific items only: startup kit (flhub-central:8002 + mTLS certs), /etc/hosts alias, data path; job code custom/client.py byte-identical to b656577.
- DoD 3: conformance 7/7 vs central :8811 (docs/report/evidence/conformance-nvflare-central.json). DoD 7: all commits Zare2001, no attribution (git log check: 0 matches); tests/unit/test_nvflare_adapter.py 7 passed (central, ~/venvs/nvflare).
- VM state left running: nvf-h1/nvf-h2 clients (cron watchdog on), relay job 9e4f07a2; island leftovers and ~/venvs/hub removed. ~/.flhub/DONE created.

### 2026-10-13T13:40Z — P4 — independent DoD re-check from the nvflare VM
- DoD 1/2: own status.sh HEALTHY (exit 0, re-run 13:3xZ); vantage6 (#81) and flower (#82) VM status replies exit 0; central aggregate HEALTHY in REPORT.md §12.
- DoD 3: docs/report/evidence/conformance-{nvflare,vantage6,flower}-central.json: exitstatus 0, max_abs_diff 0.0 in rounds 1, 2, 4 for all six sites.
- DoD 4/5/6 (W&B API, read-only, scratch venv in /tmp): project hpml_surf-surf/AgnosticFL has exactly 6 runs = the expected names, all state=finished, tags=['final'], group final-2026-10-13; mixed-6sites: 30 logged rounds, all with round/num_sites=6, sites nvf-h1/h2, v6-h3/h4, flw-h5/h6; summary equivalence/pass=True, max_abs_weight_diff=0; equivalence-final.json: 31 checkpoints, max diff 0.0.
- DoD 7: pytest at origin/main (cb19c68) in a scratch venv with nvflare 2.9.0 + flwr 1.39.0 + pandas: 40 passed, 7 skipped (conformance needs an adapter); conformance --adapter mock: 7 passed; attribution grep 0 matches, all commits Zare2001. DoD 8: REPORT.md, README.md, DECISIONS.md (7 ADRs), 4 report sections, bus transcript on main.
- DoD 9: all-done #84; DONE replies #89/#91/#93; central board DONE at 13:21:00Z after its closing commits and REPORT §12.9. Scratch venv/wandb dir removed; only ~/venvs/nvflare remains.

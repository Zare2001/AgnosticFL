# Role: NVFLARE — NVFlare adapter + NVFlare hospitals (145.38.189.142)

You make NVFlare hospitals `nvf-h1` and `nvf-h2` take part in hub rounds without hub-specific code on the hospital side. You write the server-side relay (runs on the central VM) and the standard client side (runs on your VM).

## P0 — Bootstrap

- [ ] `tools/bus health`; post `info` "nvflare agent up" with VM specs (CPU/RAM/disk).
- [ ] Verify commit attribution and hooks as described in COLLABORATION.md §3 (throwaway repo in /tmp).
- [ ] `nc -zv 145.38.205.58 8002` and `7700`; report gaps (to `central`, and to `human` if it is a cloud firewall issue).
- [ ] Create `~/venvs/nvflare` (Python 3.11) with the NVFlare version from `deploy/versions.env` (or propose one to central if not pinned yet) + torch CPU.
- [ ] With `scout`, study in the INSTALLED version: `ModelController` (`run`, `send_model_and_wait`, `sample_clients`), `FLModel` (`params`, `params_type`, `meta`, `current_round`), the Client API (`flare.init/receive/send/is_running`), the Job API (`FedJob`/`ScriptRunner`), provisioning (`project.yml`, `nvflare provision`), and the FLARE API (`new_secure_session`, `submit_job`, `get_system_info`). Write a 15-line summary in your worklog.

## P1 — Island (on your VM; goal: M1-nvflare)

- [ ] Wait for M0-core (`tools/bus wait`), `git pull --rebase`, install `flhub_core` and `adapters/common` into your venv.
- [ ] `clients/nvflare/client.py`: standard Client API loop. Receive `FLModel` → `flhub_core.train` → send `FLModel(params=…FULL…, meta={"num_examples": n, "site_id": <client name>, "train_loss": …})`. Reads its data path from the site's local config (e.g. env var or a file next to the startup kit), nothing else hub-specific.
- [ ] `adapters/nvflare/relay_controller.py`: `HubRelayController(ModelController)`; `run()` loops: poll adapter service `GET 127.0.0.1:8811/relay/next` (long-poll) → `send_model_and_wait(targets=[sites], data=FLModel(params, params_type=FULL, current_round=r))` → post results to `/relay/result`. Exits cleanly on a `shutdown` instruction.
- [ ] `adapters/nvflare/service.py`: flhub/0.1 JSON-RPC on :8811 (`discover` uses the FLARE API to list connected clients) plus the internal `/relay/*` endpoints; ensures the relay job is submitted and running (FLARE API with the admin startup kit).
- [ ] `adapters/nvflare/job/`: job definition (server: relay controller; clients: `client.py`), built with the Job API or as a job folder.
- [ ] Island: local provisioning (server name `localhost`), local server, 2 local clients with partitions 0 and 1, local hub core + orchestrator. Pass the conformance test and a 3-round run.
- [ ] Scripts:
  - `deploy/central/nvflare/{provision,install,start,stop,status}.sh` — on the central VM: venv, `project.yml` with server `flhub-central` (fed_learn 8002, admin 8003), clients `nvf-h1`, `nvf-h2`, admin user; provision into `~/flhub-runtime/nvflare/`; start server; start adapter :8811; submit the relay job.
  - `deploy/nvflare-vm/{install,start,stop,status}.sh` — on your VM: venv, install received startup kits into `~/flhub-runtime/nvflare/<site>/`, materialize data partitions, start both clients.
- [ ] Announce **M1-nvflare** with evidence (conformance output, run summary).

## P2 — Integration

- [ ] Send a `deploy-request` to central (format in COLLABORATION.md §1).
- [ ] On `deploy-result` PASS: `tools/bus get <id>` the kits, install them, stop your island server, start both clients against `flhub-central:8002`. Verify both clients are connected from the central side (ask central or check the adapter's `discover`).
- [ ] Support central's `nvflare-only` run and the mixed run; fix issues fast. **M2-nvflare** is announced by central.

## P3/P4

- [ ] Keep clients on the frozen SHA during final runs; watch client logs; report anomalies immediately.
- [ ] On `final-call`: write `docs/report/nvflare.md` (REPORT_TEMPLATE.md per-agent section), including the exact list of what a hospital installs and configures, and every problem you solved. Push, send `report-ready`.
- [ ] On `all-done`: verify your DoD items, final worklog entry, push, `touch ~/.flhub/DONE`.

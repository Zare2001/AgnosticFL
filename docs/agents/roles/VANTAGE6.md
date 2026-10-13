# Role: VANTAGE6 — vantage6 adapter + vantage6 hospitals (145.38.186.63)

You make vantage6 hospitals `v6-h3` and `v6-h4` take part in hub rounds. Hospital side = standard vantage6 nodes running a standard algorithm image. Server side (central VM) = vantage6 server + an adapter that acts as a vantage6 user. Use **vantage6 4.15.x** (Docker-based); do not use 5.x.

## P0 — Bootstrap

- [ ] `tools/bus health`; post `info` "vantage6 agent up" with VM specs. Confirm `docker ps` works without sudo (re-login if the docker group is new).
- [ ] Verify commit attribution and hooks (COLLABORATION.md §3).
- [ ] `nc -zv 145.38.205.58 7601`, `5050`, `7700`; report gaps.
- [ ] Create `~/venvs/v6` (Python 3.11) with `vantage6==4.15.*` (CLI, client, algorithm tools).
- [ ] With `scout`, study in the INSTALLED version: `v6 server new/start/import` (entities YAML with organisations, collaborations, nodes with api_keys, users), `v6 node new/start` (config: server_url, port, api_path, databases with a CSV/Parquet uri), the Python client (`Client`, `authenticate`, `setup_encryption`, `task.create`, `wait_for_results` / result retrieval), algorithm tools (`@data` decorator, `wrap_algorithm`, Dockerfile base image), and how nodes pull images (registry, local fallback, `allowed_algorithms`). 15-line summary in your worklog.

## P1 — Island (on your VM; goal: M1-vantage6)

- [ ] Wait for M0-core, `git pull --rebase`, install `flhub_core` and `adapters/common`.
- [ ] `clients/vantage6/`: algorithm package with a partial function `train(df, weights_b64, train_config)` → `{"weights_b64", "num_examples", "metrics", "site_id"}` using `flhub_core.train`; Dockerfile on the vantage6 algorithm base image with the pinned Python/torch; image `145.38.205.58:5050/flhub/v6-train:<sha>`.
- [ ] `adapters/vantage6/service.py`: flhub/0.1 JSON-RPC on :8812. Authenticates as user `hub-adapter` of org `hub-operator`; `dispatch` creates one task for the two hospital organisations (input: global weights inline, base64 safetensors; train_config); `tasks/get` maps vantage6 task/run status; `collect` decodes results, stores weights as blobs in the hub core, returns envelopes; `discover` reports node online status. Site id = organisation name (`v6-h3`, `v6-h4`).
- [ ] Island: local vantage6 server (port 7601), local registry (5050) or local image, 2 local nodes with the partition files for indices 2 and 3, local hub core + orchestrator. Pass the conformance test and a 3-round run.
- [ ] Scripts:
  - `deploy/central/vantage6/{install,start,stop,status}.sh` — on the central VM: venv, server config, `v6 server import` of `deploy/central/vantage6/entities.template.yaml` with API keys and passwords generated at install time into `~/flhub-runtime/vantage6/` (never in git), build + push the algorithm image to `localhost:5050`, start server, start adapter :8812.
  - `deploy/vantage6-vm/{install,start,stop,status}.sh` — on your VM: Docker `insecure-registries` for `145.38.205.58:5050` if needed, node configs for `v6-h3` and `v6-h4` (server `http://145.38.205.58`, port 7601, API keys from the received secret file), materialize data partitions, start both nodes.
- [ ] Announce **M1-vantage6** with evidence.

## P2 — Integration

- [ ] Send a `deploy-request` to central.
- [ ] On PASS: `tools/bus get <id>` the API keys file, reconfigure both nodes to the central server, stop your island server/registry, start nodes. Confirm both nodes show online in the adapter's `discover`.
- [ ] Support the `vantage6-only` and mixed runs.

## P3/P4

- [ ] Keep nodes on the frozen image during final runs; watch node logs.
- [ ] On `final-call`: write `docs/report/vantage6.md` (what a hospital installs/configures, image pull path, problems solved). Push, `report-ready`.
- [ ] On `all-done`: verify your DoD items, final worklog entry, push, `touch ~/.flhub/DONE`.

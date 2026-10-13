# Worklog — vantage6 agent

Newest entries at the bottom. Format: `### <UTC timestamp> — <phase> — <title>` + 2–6 bullets (what, result, evidence, next).

### 2026-10-13T12:10Z — P0 — bootstrap + vantage6 4.15.2 API study
- VM: Ubuntu 24.04.5, 4 vCPU AMD EPYC-Rome (avx2, no avx512), 31 GiB RAM, 19G disk; docker 29.9.0 without sudo. Hooks verified in a scratch repo (attribution stripped, 80-char subject blocked).
- Reachability to central: 7700 OK; 7601/5050 "connection refused" = firewall passes, no listener yet.
- `~/venvs/v6` (py3.11.17): vantage6 / vantage6-client / vantage6-algorithm-tools 4.15.2. Images pulled: `ghcr.io/vantage6/infrastructure/{server,node}:4.15.2`, `algorithm-base:4.15` (registry is ghcr.io since 4.15, not harbor2).
- API summary (installed source; server/node source read inside their images):
  1. `v6 server new` / `v6 node new` are interactive only -> write YAML by hand from `cli/template/{server,node}_config.j2`.
  2. Server configs default to SYSTEM folders: use `v6 server start -n <name> --user` (config `~/.config/vantage6/server/<name>.yaml`, sqlite in `~/.local/share/vantage6/server/<name>/`); pin `--image ...server:4.15.2`; set `ui.enabled: false`.
  3. `v6 server import <yaml> -n <name> --user` runs `vserver-local import` (server `controller/fixture.py`): organizations need ALL keys name/domain/address1/address2/zipcode/country/public_key; users nested under orgs get a superuser role; collaborations: name, encrypted, participants[{name, api_key (UUID)}] -> one node per participant.
  4. Task init org must participate in the collaboration -> `hub-operator` is a participant (its node never connects; ignored by discover).
  5. `v6 node start -n <name>` (no `--config`): config at `~/.config/vantage6/node/<name>.yaml`; pin `images.node`; CSV/parquet file must exist and is mounted at `/mnt/<label>.<ext>`; `node_extra_hosts` supported (island: `host.docker.internal: host-gateway`).
  6. Node encryption.enabled must equal collaboration.encrypted (false here). Policies: `allowed_algorithms` (regex list), `require_algorithm_pull` default true (no local-image fallback unless false). Plain-HTTP registry = host daemon `insecure-registries` (node uses host docker.sock).
  7. Client: `UserClient(host, port, path)`, `authenticate(u, p)`, `setup_encryption(None)`; `task.create(organizations, name, image, description, input_, collaboration, databases=[{"label": "default"}])`; input = json -> base64 (no size check); `task.get(id)` status in {pending, initializing, active, completed, failed, start failed, non-existing Docker image, crashed, killed by user, not allowed, unknown error}; `result.from_task(id)["data"][i]["result"]` is a JSON *string*; `task.kill(id)`; `node.list(collaboration=, is_online=)`.
  8. Algorithm: `wrap_algorithm()` reads PKG_NAME, INPUT_FILE (json {method, args, kwargs}), OUTPUT_FILE (json.dumps -> no numpy/bytes); `@data(1)` passes `pd.read_csv(uri)` / `pd.read_parquet`; `@metadata` gives task_id/node_id/organization_id (no org name).
  9. Official algorithm-base:4.15 = python:3.10-slim + vantage6-common + vantage6-algorithm-tools (numpy 2.2.6, pandas 2.3.3, no pyarrow) -> our image: python:3.11-slim + vantage6-algorithm-tools==4.15.2 + pinned torch (ADR).
- Next: wait for M0-core; meanwhile server/entities/node templates and deploy scripts.

### 2026-10-13T12:40Z — P1 — M1-vantage6 island passes
- Island on this VM, built only with the scripts: `deploy/central/vantage6/install.sh --island && start.sh` (vantage6 server 4.15.2 :7601, registry 127.0.0.1:5050, adapter :8812) + `deploy/vantage6-vm/install.sh --island --keys <file> && start.sh` (nodes v6-h3/v6-h4) + hub core :8800. Second run of every script = no changes, all healthy (status.sh exit 0).
- Conformance `pytest tests/conformance --adapter http://127.0.0.1:8812`: 7 passed in 26 s; max_abs_diff 0.0 in rounds 1, 2 (after busy -32015) and 4 (after cancel); n=785/785; code 0.1.0+9c6b064c1e96; ~7.5 s per round (node pulls image + container start).
- 3-round orchestrator run `deploy/central/vantage6/island-3r.yaml` (W&B offline): finished, 8 s/round; vs `hub.sim` same config: equivalence_check max abs diff 0.000e+00 rounds 0-3, acc diff 0.0 pp -> PASS.
- Adapter design: vantage6 user `hub-adapter` (org hub-operator, participant of collaboration `flhub`); dispatch -> one v6 task (method train, model blob bytes inline base64, train_config+round, {org_id: site_id}); poll `task/<id>/status` (runs every 5th poll for progress); results -> weights blob bytes stored as-is in hub core (weights_ref); checks site_id and base_model echoed by the algorithm; cancel/deadline -> `task.kill`.
- Problems solved: (1) py3.11 CLI bugs (ADR-005); (2) `UserClient` hijacks the root logger -> restore handlers/level, own logger; (3) start.sh `&` applied to `cd && nohup` list -> subshell held the pipe and pid file had the wrong pid; (4) root-owned log files from server/node containers break `v6 node stop` (exit 1) -> docker stop.
- Next: deploy-request to central; then switch nodes to central (insecure-registries for 145.38.205.58:5050), stop island.

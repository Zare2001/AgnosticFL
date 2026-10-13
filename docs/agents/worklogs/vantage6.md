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

### 2026-10-13T12:55Z — P2 — M2-vantage6 on central; hospital VM cleaned
- Central deploy-result PASS (bus #39, ran at bd800ce): server 4.15.2 :7601, image 145.38.205.58:5050/flhub/v6-train:bd800cee7922, adapter 127.0.0.1:8812. Node API keys received as bus secret file #3 -> `deploy/vantage6-vm/install.sh --keys` (file consumed; stored in ~/flhub-runtime/vantage6/node-keys.env, 600).
- Switch: island stopped (server, adapter, hub core, registry); install.sh added `145.38.205.58:5050` to /etc/docker/daemon.json insecure-registries (Docker restarted); image pre-pulled in 10 s; both nodes connected to http://145.38.205.58:7601.
- Central M2-vantage6 (bus #50): conformance 7/7 vs central :8812 (diff 0.0 rounds 1,2,4); `--only vantage6 --rounds 3` == hub.sim (diff 0.0), 7-8 s/round.
- DoD 2 cleanup: removed island server config/db/logs, island secrets, ~/venvs/hub, island images (server, algorithm-base, registry, local v6-train/probe), raw dataset; ~/venvs/v6 recreated with vantage6 4.15.2 only. What stays on this VM: Docker (+ insecure-registries entry), ~/venvs/v6 (vantage6 CLI), node image ghcr.io/vantage6/infrastructure/node:4.15.2, node configs ~/.config/vantage6/node/v6-h{3,4}.yaml, node-keys.env, data ~/flhub-data/v6-h{3,4}.csv (+ ~/venvs/flhub-data, simulation only), algorithm image pulled by the nodes.
- central status.sh now exits 1 when a site is offline (central's request; --no-sites to skip) [9c88bca].
- Node watchdog: install.sh adds a cron entry that re-runs start.sh every minute while ~/flhub-runtime/vantage6/nodes.enabled exists (start.sh sets, stop.sh clears; flock). Test: `docker kill vantage6-v6-h4-user` -> back and connected after 15 s.

### 2026-10-13T13:15Z — P3/P4 — final runs served; report section
- Freeze b656577 confirmed (bus #61): `git diff bd800ce b656577 -- flhub_core clients/vantage6` empty; nodes run 145.38.205.58:5050/flhub/v6-train:bd800cee7922 @ sha256:262fe79378fa…(local == registry digest), code 0.1.0+9c6b064c1e96; status.sh OK.
- M3-final (bus #66): vantage6-only `twk49dhp` acc 0.8638 / AUC 0.9246, 238 s; mixed-6sites `iehjnc44` acc 0.8654 / AUC 0.9247 == sim reference (diff 0.0 rounds 0-30). Each node ran 60 tasks in the final runs, 0 node errors.
- Island evidence summaries committed (8892003); `docs/report/vantage6.md` written per final-call (#67): footprint, reproduce steps, problems/time lost, DoD 1/2 status output.
- Next: report-ready; keep nodes up until all-done; then verify DoD items and touch ~/.flhub/DONE.

# vantage6 — per-agent report

Agent `vantage6`, VM 145.38.186.63 (Ubuntu 24.04.5, 4 vCPU AMD EPYC-Rome / AVX2, 31 GiB RAM, Docker 29.9.0).
Hospitals `v6-h3` and `v6-h4` (partitions 2 and 3, 785 examples each). vantage6 4.15.2 throughout.

## 1. Scope and ownership

| Path | What |
|---|---|
| `clients/vantage6/` | the vantage6 algorithm `v6_flhub.train` and its image (`Dockerfile`, `Dockerfile.dockerignore`, `pyproject.toml`) |
| `adapters/vantage6/` | the flhub/0.1 adapter (`python -m adapters.vantage6`, 127.0.0.1:8812) |
| `deploy/central/vantage6/` | central VM: `install/start/stop/status.sh`, `server.template.yaml`, `entities.template.yaml`, `island-3r.yaml` |
| `deploy/vantage6-vm/` | hospital VM: `install/start/stop/status.sh`, `node.template.yaml` |
| `tests/unit/test_vantage6_adapter.py` | 5 adapter unit tests with a fake vantage6 client |
| `docs/DECISIONS.md` | ADR-004 (algorithm image on Python 3.11), ADR-005 (vantage6 CLI workarounds on Python 3.11) |

## 2. What I built

**Algorithm** (`clients/vantage6/v6_flhub/__init__.py`): the standard vantage6 pattern, one method `train`, wrapped with `@metadata` and `@data(1)`:

```python
def train(df, meta, weights_b64, train_config, sites=None) -> dict
```

- The node passes its database as a DataFrame. `flhub_core.data.load_site(df)` converts it and `flhub_core.train.train()` runs one round.
- The algorithm returns JSON: `weights_b64` (base64 safetensors), `num_examples`, `metrics`, `site_id`, `organization_id`, `base_model` (sha256 of the received bytes) and `client_code_version`.
- `site_id` is looked up as `sites[str(meta.organization_id)]`: vantage6 gives an algorithm its organisation *id*, not its name.
- The image `<registry>/flhub/v6-train:<git sha>` is built FROM `python:3.11.17-slim-bookworm`. It contains what the official `algorithm-base:4.15` adds (`vantage6-algorithm-tools==4.15.2`, pandas 2.3.3) plus the pinned torch 2.14.1+cpu, numpy 2.4.4, safetensors 0.8.0, `flhub_core` and `v6_flhub`. CMD is the standard `wrap_algorithm()` (ADR-004).

**Adapter** (`adapters/vantage6/service.py`, a `BaseAdapter` subclass). It acts as vantage6 user `hub-adapter` of organisation `hub-operator`. That organisation must participate in the collaboration `flhub` (unencrypted), because vantage6 only lets participants create tasks.

| flhub/0.1 | vantage6 4.15.2 client calls |
|---|---|
| `discover` | `client.node.list(collaboration=…)`: node `status == "online"` per organisation; `client.util.get_server_version()` |
| `dispatch` | One `client.task.create(collaboration, organizations=[ids], name="flhub-r<n>", image, description, input_={"method": "train", "kwargs": {weights_b64, train_config + round, sites: {org_id: site_id}}}, databases=[{"label": "default"}])`. The model blob bytes are sent unchanged. |
| `tasks/get` | Every 1 s `client.request("task/<id>/status")`; every 5th poll `client.run.from_task` for progress. A failed run state ends the round with the run logs in the error (-32014). |
| `collect` | `client.result.from_task` (each `result` is a JSON string). Run id → organisation → site. Checks `site_id` and `base_model`; the weights bytes go into the hub core unchanged. |
| `cancel`, deadline | `client.task.kill(id)` |

**Scripts.**
- `deploy/central/vantage6/install.sh` generates secrets, renders the server config, imports the entities once, then builds and pushes the image.
- `start.sh` starts the server and the adapter. The adapter is restarted when `adapter.env` changes, for example after a new image tag.
- `status.sh` exits 1 unless the server, the image in the registry, the adapter and both sites are up.
- `deploy/vantage6-vm/*` covers the hospital side (§4); every script is idempotent.

## 3. Framework specifics (vantage6 4.15.2)

- **Images:** they moved to `ghcr.io/vantage6/infrastructure/{server,node}:4.15.2` (no longer harbor2).
  - The node image tag defaults to the server's `major.minor`, so it is pinned via `images.node`.
- **Configs:** `v6 server new` and `v6 node new` are interactive only, so both configs are rendered from templates. The templates follow `vantage6/cli/template/{server,node}_config.j2`.
  - Server config: `ui.enabled: false`, `image`/`images.server` pinned, sqlite.
- **Entities** (`v6 server import`, server `controller/fixture.py`):
  - Organisations need *all* of `name, domain, address1, address2, zipcode, country, public_key`.
  - Users nested in an organisation get a superuser role.
  - `collaborations[].participants[]` = `{name, api_key}`, where `api_key` must be a valid UUID; each participant becomes one node.
- **Server first start:** without a user named `root`, the server creates superuser `root`/`root`. Our import creates `root` with a generated password.
- **Encryption:** node `encryption.enabled` must equal the collaboration's `encrypted` (both false here).
- **Policies:** `allowed_algorithms` entries containing regex metacharacters are matched with `re.match`.
  - `require_algorithm_pull` defaults to true: the image is pulled for every task, with no local fallback.
  - The node pulls through the host Docker socket, so a plain-HTTP registry needs only the host daemon's `insecure-registries`.
- **Algorithm tools:**
  - `wrap_algorithm()` reads `PKG_NAME`, `INPUT_FILE` (`{method, args, kwargs}`) and `OUTPUT_FILE` (`json.dumps`, so no numpy or bytes).
  - `@data(1)` loads the CSV with `pd.read_csv(path)` using default arguments; this is bit-exact for integer pixels.
  - `@metadata` gives `task_id`, `node_id`, `organization_id`.
- **Task status:** aggregated from run states. Any failed run gives a failure status; the task is `completed` only when every run is.
- **Surprises:**
  - str-Enum formatting bugs in the CLI on Python ≥ 3.11 (ADR-005).
  - `UserClient()` clears the root logger's handlers and sets its level.
  - Server and node containers write the CLI's log directory as root.

## 4. Hospital-side footprint

What one hospital needs is the standard vantage6 node install. This is what `deploy/vantage6-vm/install.sh` + `start.sh` do for both hospitals:

```bash
pip install vantage6==4.15.2                    # in ~/venvs/v6 (vantage6, -client, -common + deps; nothing else)
docker pull ghcr.io/vantage6/infrastructure/node:4.15.2
# /etc/docker/daemon.json: {"insecure-registries": ["145.38.205.58:5050"]}   (operator registry is plain HTTP)
# ~/.config/vantage6/node/v6-h3.yaml (same for v6-h4), see below
v6 node start -n v6-h3 --user
```

Node config (the format `v6 node new` would write; API key redacted):

```yaml
api_key: ***                      # from the operator (credential)
server_url: http://145.38.205.58  # connection setting
port: 7601
api_path: /api
task_dir: ~/.local/share/vantage6/node/v6-h3
databases: [{label: default, uri: ~/flhub-data/v6-h3.csv, type: csv}]   # the hospital's own data file
encryption: {enabled: false, private_key: null}
images: {node: ghcr.io/vantage6/infrastructure/node:4.15.2}
policies:
  allowed_algorithms: ['^145\.38\.205\.58:5050/flhub/v6-train(:[A-Za-z0-9._-]+)?(@sha256:[a-f0-9]{64})?$']
logging: {...}                    # template defaults
```

- **What code runs at the hospital and how it arrives:** for every task, the node (`ghcr.io/vantage6/infrastructure/node:4.15.2`, unmodified) pulls the algorithm image `145.38.205.58:5050/flhub/v6-train:bd800cee7922` from the operator's registry. It then runs the image in its sandboxed algorithm container. That image holds `flhub_core` + `v6_flhub` behind the standard `wrap_algorithm()` entry point.
- **Hub-specific items:**
  - the server address and port
  - the API key
  - the `allowed_algorithms` policy, which names the operator's image
  - the registry in `insecure-registries`
  - the data path

  Nothing hub-specific is installed on the host itself.
- **Evidence** (after the final runs):
  - `~/venvs/v6` contains vantage6 4.15.2 with vantage6-client and vantage6-common, 64 packages, and no torch or flhub_core.
  - `docker images` shows only the node image and `v6-train:bd800cee7922`.
- **Simulation-only extras:**
  - `~/venvs/flhub-data` (numpy + `flhub_core.data`) creates the site CSVs; a real hospital already has its file.
  - The agent's repo checkout holds the scripts.
  - A watchdog cron entry re-runs the standard `v6 node start` if a node container disappears.

## 5. Reproduce from scratch (vantage6 parts)

1. **Central VM** (after central's `deploy/central/install.sh`, with registry :5050 and hub core :8800 up):
   ```bash
   bash deploy/central/vantage6/install.sh && bash deploy/central/vantage6/start.sh
   ```
   Check with `bash deploy/central/vantage6/status.sh --no-sites`. Then send the node keys:
   ```bash
   tools/bus put ~/flhub-runtime/vantage6/v6-nodes.env --to vantage6 --secret
   ```
2. **vantage6 VM:**
   ```bash
   tools/bus get <id> -o /tmp/v6-nodes.env
   bash deploy/vantage6-vm/install.sh --keys /tmp/v6-nodes.env   # consumes the file; adds insecure-registries, restarts Docker
   bash deploy/vantage6-vm/start.sh                             # waits until both nodes are connected
   bash deploy/vantage6-vm/status.sh
   ```
3. **Central VM:**
   ```bash
   bash deploy/central/vantage6/status.sh        # all online
   pytest tests/conformance --adapter http://127.0.0.1:8812
   ```
4. **P1 island on one VM:** use `install.sh --island` on both sides. This gives a local registry on 127.0.0.1:5050 and lets the nodes reach the server through `host.docker.internal`; then follow `deploy/central/vantage6/island-3r.yaml`.

Credentials:
- The operator's install generates `~/flhub-runtime/vantage6/secrets.env` (mode 600): JWT key, `root` and `hub-adapter` passwords, node API keys.
- The node keys reach the hospital only via `tools/bus put --secret`, and are stored in `~/flhub-runtime/vantage6/node-keys.env` (mode 600).

## 6. Timeline (UTC, 2026-10-13)

| Time | Event | Ref |
|---|---|---|
| 11:59 | agent up; vantage6 4.15.2 venv, server/node images pulled | bus #1 |
| 12:15 | `flhub_core.selfcheck` bit-exact on the host and inside the algorithm image (sha256:7c80a5a3…) | bus #14 |
| 12:18 | algorithm + image, deploy templates, ADR-004/005 | 365d898 |
| 12:35 | adapter, central and VM scripts; **M1-vantage6** (island); deploy-request | fc4a41c, bus #31, #32 |
| 12:42 | central deploy PASS at bd800ce; node keys as bus secret file #3 | bus #38, #39 |
| 12:43 | nodes switched to the central server; island removed, hospital VM cleaned | bus #41 |
| 12:46 | **M2-vantage6** (central: conformance 7/7, 3 rounds == sim) | bus #50 |
| 12:49 | node watchdog; status.sh fails when a site is offline | 9c88bca, 88fef56 |
| 12:52 | freeze b656577 confirmed: no diff in `flhub_core`/`clients/vantage6` vs bd800ce; image digest sha256:262fe793…; code 0.1.0+9c6b064c1e96 | bus #56, #61 |
| 13:09 | **M3-final**: vantage6-only and mixed-6sites served by v6-h3/v6-h4 (60 tasks per node, 0 errors) | bus #66 |

## 7. Problems and fixes

| Problem | Component | Symptom | Root cause | Fix | Commit | Time lost |
|---|---|---|---|---|---|---|
| Import lost | `v6 server import` on py3.11 | login fails; server log "No root user found! Is this the first run?" | Python 3.11 formats an Enum member used as an env-var name as `ServerGlobals.DB_URI_ENV_VAR` instead of `VANTAGE6_DB_URI`, so the import writes into the throw-away container's DB | install.sh runs the server image's own `vserver-local import` with `VANTAGE6_DB_URI` (ADR-005) | 31b5f3d | 15 min |
| Server cannot be stopped | `v6 server stop` | "flhub is not running!" | the same Enum bug names the container `…-ServerType.V6SERVER`, while stop looks for `…-server` | find and stop the server by Docker labels | 31b5f3d | 5 min |
| Default superuser | server first start | `root`/`root` created | the server creates it when no user `root` exists | the import creates `root` with a generated password | 4f2f545 | 3 min |
| CLI log permission | `v6 node stop`, CLI calls | "Can't write to log dir", exit 1 | containers (root) write the CLI's log files | server via `-c` (no CLI file log); nodes stopped with `docker stop` | 31b5f3d, 511c694 | 5 min |
| Adapter logs vanished | adapter | own INFO lines missing | `UserClient()` replaces the root logger's handlers, level WARN | restore handlers/level; quiet `vantage6.client` logger | a26514d | 5 min |
| start.sh hung | central start.sh | `start.sh \| tail` never returned; pid file had the wrong pid | `&` applied to `cd && nohup …`; the subshell kept stdout | background only the python command, stdin `/dev/null` | 31b5f3d | 7 min |
| Empty crontab | VM install.sh | watchdog not installed, script stopped early | `crontab -l` exits 1 without a crontab (`set -e` + `pipefail`) | tolerate both exits | 88fef56 | 3 min |
| Python 3.10 base | algorithm image | official `algorithm-base:4.15` is py3.10 | plan pins py3.11 and identical torch/numpy | own image on `python:3.11.17-slim` (ADR-004) | 30eccd3 | 5 min |

Dead ends:
- `v6 server stop -n` and `v6 node stop` (Enum and log-permission bugs) were replaced by Docker label/name stops.
- I considered a separate Python 3.10 venv for the vantage6 CLI, then dropped it in favour of two explicit workarounds.
- A probe algorithm image (8 MB payload echo) proved inline base64 payloads are fine; it was deleted afterwards.

## 8. Tests and evidence

- **Determinism:** selfcheck gives the same hash on the host venv and inside the image (`--network none`), identical to the other three VMs.
- **Island** (`docs/report/evidence/{conformance,equivalence}-vantage6-island.json`):
  - Conformance 7/7, max_abs_diff 0.0 in rounds 1, 2 and 4.
  - 3-round orchestrator run == `hub.sim`: diff 0.0 in rounds 0–3, 0.0 pp.
- **Central** (`docs/report/evidence/conformance-vantage6-central.json`, bus #50):
  - Conformance 7/7, diff 0.0.
  - `--only vantage6 --rounds 3` == sim.
- **Final runs** (W&B `hpml_surf-surf/AgnosticFL`, tag `final`, at b656577):

  | Run | W&B id | Rounds | Acc | AUC | Duration |
  |---|---|---|---|---|---|
  | vantage6-only | `twk49dhp` | 30 | 0.8638 | 0.9246 | 238 s (about 8 s/round) |
  | mixed-6sites | `iehjnc44` | 30 | 0.8654 | 0.9247 | 244 s |

  mixed-6sites == sim-fedavg-reference (`kur37gs2`): diff 0.0 in rounds 0–30.
- **Unit tests:** `tests/unit/test_vantage6_adapter.py`, 5 passed. They also pass without vantage6 installed.
- **DoD 1/2:** final `bash deploy/vantage6-vm/status.sh` (exit 0):
  ```
  v6-h3  OK    connected  ghcr.io/vantage6/infrastructure/node:4.15.2 -> http://145.38.205.58:7601 (central)
        last task_id=70
  v6-h4  OK    connected  ghcr.io/vantage6/infrastructure/node:4.15.2 -> http://145.38.205.58:7601 (central)
        last task_id=70
  ```
- **Watchdog:** after `docker kill vantage6-v6-h4-user`, the node was back and connected in 15 s.

## 9. Open issues / recommendations

- **Transport:** the vantage6 API (:7601) and the registry (:5050) are plain HTTP, limited by the cloud firewall to the VM IPs. Production should add an HTTPS reverse proxy plus a registry with TLS and authentication (nodes support `docker_registries` credentials).
- **Payload encryption:** collaboration encryption is off, so the server stores task inputs and results (weights) in plaintext. `encrypted: true` with per-organisation keys would not change the adapter mapping.
- **Image pinning:** pin the image by digest in the task (`…@sha256:…`; the policy regex already accepts it), or use a vantage6 algorithm store.
- **Latency:** about 8 s per round, mostly the node's per-task image pull check and container start. vantage6 5.x sessions or a long-running algorithm would cut this.
- **Upstream:** report the Python 3.11 Enum formatting bugs in the vantage6 4.15 CLI.

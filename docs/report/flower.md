# Flower agent report (flower VM 145.38.187.20)

## 1. Scope and ownership

The Flower slice of AgnosticFL. The hospitals `flw-h5` and `flw-h6` are standard Flower SuperNodes on the flower VM. On the central VM, the hub talks to them through a Flower SuperLink, a relay ServerApp and the Flower adapter.

| Path | What |
|---|---|
| `clients/flower/` | Flower app (FAB): standard `ClientApp` (hospitals) + hub relay `ServerApp` (central only) |
| `adapters/flower/service.py` | Flower adapter: flhub/0.1 on `127.0.0.1:8813` (`RelayAdapter`) + relay launcher |
| `deploy/central/flower/` | central VM: install / start / stop / status of SuperLink + adapter |
| `deploy/flower-vm/` | flower VM: install / start / stop / status of the 2 SuperNodes, `supernode.sh` restart loop, island config |
| `tests/flower/test_flower_relay.py` | relay + adapter tests with a fake `Grid` that runs the real ClientApp |
| ADR-007 (`docs/DECISIONS.md`) | Flower 1.39 deployment decisions |

## 2. What was built

One round, Flower part:

1. The orchestrator calls `dispatch` on the adapter (`127.0.0.1:8813`). `RelayAdapter` queues a job: global model ref + `config` (dispatch `train_config` + `round`).
2. The relay ServerApp runs inside one long-lived Flower run on the central SuperLink. It long-polls `GET /relay/next` (`RelayClient.next_job`) and loads the global weights from the hub core (`RelayClient.get_model`).
3. The relay sends one standard TRAIN `Message` per site through the `Grid`. The content is a `RecordDict`:
   - `"arrays"`: an `ArrayRecord` keyed by the PyTorch `state_dict` names;
   - `"config"`: a `ConfigRecord` holding the job config.

   `dst_node_id` is the site's SuperNode, `ttl` is the remaining deadline and `group_id` is the round.
4. Each SuperNode pulls its message over the Fleet API (`145.38.205.58:9092`) and fetches the FAB from the SuperLink. `@app.train()` then runs `flhub_core.train(weights, load_site(data-path), config)` and replies with:
   - `"arrays"`: the new weights;
   - `"metrics"`: a `MetricRecord` with `num-examples`, `train_loss`, `train_acc`;
   - `"site-info"`: `site-id` and `client-code-version`.
5. The relay pulls the replies, stores each site's weights in the hub core (`RelayClient.result`, a blob ref) and posts `/relay/progress` and `/relay/result`. If a site fails, it posts one error that names it.
6. `RelayAdapter` builds the envelopes (`tasks/get` → `completed`, then `collect`).

Design choices:

- **Relay = standard ServerApp in a long-lived run.** The adapter's `RelayLauncher` keeps exactly one relay run alive. If no poll or progress arrives for 45 s, it runs `flwr run <repo>/clients/flower flhub --run-config "adapter-url=... hub-url=..."` against the SuperLink's local HTTP API (`127.0.0.1:9093`) and `flwr stop`s the run it replaced. On stop, it delivers `shutdown` to the relay.
- **Node id → site by a standard QUERY.** The ServerApp cannot see `--node-config`, and node ids are random per start. For every new online node, the relay sends a QUERY that `@app.query()` answers with its `site-id`.
  - The query is non-blocking: a dead but still-listed node cannot stall the loop.
  - If two nodes claim one site (old id not yet expired), the newest answer wins.
  - Mapped sites go to `/relay/sites`, which feeds `discover`.
- **Fail fast.**
  - A site without a SuperNode fails the round at once.
  - A node that goes offline mid-round fails it within about 5 s, not after `deadline_s`.
  - A cancelled job is noticed through `/relay/status`, and a late-result 404 is ignored.
- **Bit-exact transport.** `ArrayRecord({name: Array(ndarray)})` serializes via `np.save` and keeps names and order.
- **Resilience (ADR-007).**
  - The SuperLink runs with `--database`, so SuperNode ids survive its restart.
  - `supernode.sh` restarts `flower-supernode` if it exits.
  - A cron watchdog re-runs the idempotent `start.sh` every minute while the nodes are enabled.

## 3. Framework specifics (Flower 1.39.0, verified in the installed source)

APIs used:
- ServerApp: `flwr.serverapp.ServerApp`, `@app.main()` → `main(grid: Grid, context: Context)`.
- Grid: `Grid.get_node_ids()` (ONLINE nodes only), `Grid.push_messages()`, `Grid.pull_messages()`.
- Messages: `flwr.app.Message(content, dst_node_id, MessageType.TRAIN|QUERY, ttl=, group_id=)`, `Message(content, reply_to=msg)`, `has_error()`, `error.reason`.
- Records: `RecordDict`, `ArrayRecord`, `Array(ndarray)`, `Array.numpy()`, `ConfigRecord`, `MetricRecord`.
- ClientApp: `flwr.clientapp.ClientApp`, `@app.train()`, `@app.query()`; `Context.node_config`, `Context.run_config`.
- CLI: `flower-superlink`, `flower-supernode`, `flwr run|stop|log`, connection file `$FLWR_HOME/config.toml` (`[superlink.flhub] address="127.0.0.1:9093" insecure=true`).

Surprises compared with the plan:
1. **One HTTP API on the SuperLink.** The ServerAppIo gRPC API (port 9091) is gone. `flwr run` and the ServerApp (`HttpGrid`) share one HTTP API on `--host/--port`. We use `127.0.0.1:9093`; 9091 is unused.
2. **No ClientAppIo address.** `flower-supernode --clientappio-api-address` is removed. Each SuperNode has its own local `--port` (9094/9095).
3. **New `flwr run` config.** It ignores `[tool.flwr.federations]` and uses `$FLWR_HOME/config.toml` connections. It returns right after submitting.
4. **Runtime dependency install.** The SuperLink installs FAB dependencies at runtime by default; we disable that. SuperNodes do not install them.
5. **Executables found via `PATH`.** `flower-superexec`, `flwr-serverapp` and `flwr-clientapp` are started by bare name, so the venv `bin` must be on `PATH`. `flwr-clientapp` is a fresh process per message (about 1–2 s overhead).
6. **SuperNode exits on `UNAUTHENTICATED`.** A SuperNode the SuperLink no longer knows sends itself SIGINT and logs "terminated gracefully".
7. **Testing outside a run.** `Message(...)` outside a run needs `TaskIdentity` set.
8. **FAB limits.** The FAB is at most 10 MiB and holds `*.py, *.toml, *.md, ...` only. `flwr new` needs the Flower Platform API (network access).

## 4. Hospital-side footprint (flw-h5, flw-h6)

Install (`bash deploy/flower-vm/install.sh`, idempotent):
```
uv venv --python 3.11.17 ~/venvs/flower
uv pip install --python ~/venvs/flower/bin/python "torch==2.14.1+cpu" --index-url https://download.pytorch.org/whl/cpu
uv pip install --python ~/venvs/flower/bin/python flwr==1.39.0 numpy==2.4.4 safetensors==0.8.0 -e ~/AgnosticFL/flhub_core
~/venvs/flower/bin/python -m flhub_core.data materialize --sites flw-h5 flw-h6     # ~/flhub-data/<site>.csv, sha256-checked
```
The install also adds the watchdog cron entry.

Configure and run (`bash deploy/flower-vm/start.sh`). This is the standard SuperNode command; only the address and node config are ours:
```
flower-supernode --insecure --superlink 145.38.205.58:9092 --port 9094 \
    --node-config "site-id='flw-h5' data-path='/home/<user>/flhub-data/flw-h5.csv'"     # flw-h6: --port 9095
```

How the code arrives and what runs:
- The ClientApp arrives in the FAB that `flwr run` ships from the operator's SuperLink (standard Flower).
- A SuperNode loads only `[tool.flwr.app.components].clientapp` (`flhub_flower.client_app:app`; checked in `flwr/clientapp/utils.py`). That is the documented Message-API pattern:
  - `@app.train()`: `ArrayRecord` + `ConfigRecord` in → `flhub_core.train` → `ArrayRecord` + `MetricRecord` out;
  - `@app.query()`: returns `site-id`.
- The relay ServerApp ships in the same FAB but is never imported on a hospital.

Hub-specific items on the flower VM:
- connection settings: `--superlink 145.38.205.58:9092`;
- node config: `site-id`, `data-path`;
- the shared training library `flhub_core`.

There are no credentials (insecure Flower, firewall-restricted). Process supervision (`supernode.sh`, cron) is operational only.

Evidence:
- `import adapters` and `import hub` fail in `~/venvs/flower`; its only project packages are `flhub-core`, `flwr` and `torch`.
- SuperNodes run with cwd `~/flhub-runtime/flower/<site>`, outside the repo.
- Conformance (island and central) and all final runs passed with this venv.
- Every envelope carries `client_code_version` 0.1.0+9c6b064c1e96.

## 5. Timeline (UTC, bus message ids)

| Time | Event |
|---|---|
| 12:00 | agent up (#4); hooks verified; `~/venvs/flower` with flwr 1.39.0 + torch 2.14.1+cpu |
| 12:04 | Flower 1.39 port-map change sent to central (#7) |
| 12:11 | selfcheck bit-exact with central (#11) |
| 12:13 | first end-to-end round through a local SuperLink + 2 SuperNodes, diff 0.0 |
| 12:14 | first push 661236f (FAB, relay, adapter) (#13) |
| 12:44 | 7f5adec: adapter + relay on `adapters.common`, deploy scripts, tests, ADR-007 (#44). **M1-flower** (#45), deploy-request (#46) |
| 12:46 | 8b19ca5 status.sh exit codes (#47); deploy-result PASS (#49) |
| 12:47 | SuperNodes switched to 145.38.205.58:9092 (#51) |
| 12:50 | **M2-flower** (#54): central conformance 7/7, diff 0.0; flower-p2 == sim |
| 12:51 | M2-mixed (#55); freeze b656577 (#56) |
| 12:52 | d0ce5ef watchdog (#57); freeze confirmed (#59) |
| 13:09 | **M3-final** (#66): flower-only and mixed-6sites finished; mixed == sim, diff 0.0 |

## 6. Problems and fixes

| Problem | Component | Symptom | Root cause | Fix | Commit | Time lost (est.) |
|---|---|---|---|---|---|---|
| Plan ports 9091 / ClientAppIo missing | SuperLink, SuperNode | flags absent in `--help` | Flower 1.39 has one HTTP API per process | `--host 127.0.0.1 --port 9093`; SuperNode `--port 9094/9095`; ADR-007 | 7f5adec | 10 min |
| Hospitals vanish after a SuperLink restart | SuperNode | "SuperNode terminated gracefully" on both | in-memory SuperLink forgot the node ids → `UNAUTHENTICATED` → self-SIGINT | `--database`; `supernode.sh` restart loop (same DB keeps ids; wiped DB → back in ~10 s) | 7f5adec | 15 min |
| Stale node id after a SuperNode restart | relay | old id listed ONLINE until its heartbeat expires | random ids, heartbeat-based offline tagging | QUERY mapping, newest node per site wins, old id ignored | ccc26dc | 5 min |
| Relay loop could block 60 s (dead end: blocking query) | relay | adapter would think the relay is dead and launch a second one | blocking wait for query replies | queries pushed once, replies collected on later refreshes | ccc26dc | 5 min |
| Dead node mid-round waits `deadline_s` | relay | round hangs up to 1800 s | no liveness check during the wait | destinations leaving `get_node_ids()` fail the round in ~5 s | ccc26dc | 5 min |
| Adapter not stopped via its pidfile | deploy | "Address already in use" on restart | `(cd .. && setsid .. &)`: `$!` was a wrapper subshell | `cd` at script level, background the command directly | 7f5adec | 10 min |
| Relay looks dead during long rounds | adapter | (review) `RelayAdapter` only tracks polls | no polls while a round runs | `/relay/progress` every 10 s counts as contact | 998122e | 5 min |
| Own task book duplicated `adapters.common` (dead end) | adapter | two implementations of the same protocol | built before M0-core landed | ported to `RelayAdapter`/`RelayClient`, own task book removed | 998122e, 62d9d09 | 25 min |
| Relay rewrite > 400 changed lines | git | pre-push size rule | port + features in one file | committed a tested intermediate (ccc26dc) first | ccc26dc | 10 min |
| `pkill -f` killed the agent's own shell | ops | exit 144 | the pattern matched the shell's own command line | kill only by pidfile or by listening port | – | 3 min |

## 7. Tests and evidence

- `tests/flower/test_flower_relay.py`: 5 tests, about 6 s, run with `~/venvs/flower-central/bin/python -m pytest tests/flower`:
  - bit-exact round vs `flhub_core.train`;
  - a restarted node replaces its stale predecessor;
  - missing and lost sites fail fast;
  - cancel, then the next round succeeds;
  - relay liveness and shutdown.
- Island (M1), `docs/report/evidence/`:
  - `conformance-flower-island.json`: 7/7, diff 0.0 in rounds 1, 2 and 4, n=784/784;
  - `equivalence-flower-island.json`: 3 rounds == `hub.sim`, diff 0.0.
- Central (M2): `conformance-flower-central.json` 7/7, diff 0.0; flower-p2 == sim; mixed-p2 == sim.
- Final runs (M3, W&B `hpml_surf-surf/AgnosticFL`, tag `final`, at b656577):

| Run | W&B id | Rounds | Test acc | Test AUC | Duration |
|---|---|---|---|---|---|
| flower-only | 011lasv0 | 30 | 0.7580 | 0.9149 | 221 s |
| mixed-6sites | iehjnc44 | 30 | 0.8654 | 0.9247 | 244 s |

  mixed-6sites == sim-fedavg-reference: max abs weight diff 0.0 in rounds 0–30, final acc diff 0.0 pp (`equivalence-final.json`). No ERROR lines in either SuperNode log during the final runs.
- DoD 1/2, `bash deploy/flower-vm/status.sh` at 2026-10-13T13:10Z (exit 0):
  ```
  flw-h5: running pid 85904, connected to 145.38.205.58:9092, SuperNode ID: 7949874069104181677
  flw-h6: running pid 89115, connected to 145.38.205.58:9092, SuperNode ID: 10935481905872741677
  watchdog: enabled (cron, superlink 145.38.205.58:9092)
  flower-vm: HEALTHY
  ```

Reproduce from scratch:
1. On central:
   ```
   bash deploy/central/flower/install.sh && bash deploy/central/flower/start.sh
   bash deploy/central/flower/status.sh --no-sites
   ```
2. On the flower VM:
   ```
   git clone <repo> ~/AgnosticFL && cd ~/AgnosticFL
   bash deploy/flower-vm/install.sh && bash deploy/flower-vm/start.sh && bash deploy/flower-vm/status.sh
   ```
3. On central: `bash deploy/central/flower/status.sh` should print READY (both sites online about 30 s after the SuperNodes start).

No credentials are exchanged. For an island on one VM, use `FLOWER_FLEET_BIND=127.0.0.1:9092` for the central scripts, `FLHUB_SUPERLINK=127.0.0.1:9092` for `deploy/flower-vm/start.sh`, `python -m hub.core --port 8800`, and `deploy/flower-vm/island-3r.yaml`.

## 8. Open issues / recommendations

- **Security.** Flower runs without TLS and without SuperNode authentication, restricted by the cloud firewall (ADR-007). Next steps:
  - SuperLink `--ssl-certfile/--ssl-keyfile/--ssl-ca-certfile`, and `--root-certificates` on the SuperNodes;
  - `--enable-supernode-auth` with one key pair per hospital.
- **SuperLink access log.** The SuperLink logs every HTTP request (`uvicorn.access`); long-lived deployments should use `--log-file` with rotation.
- **Relay log.** The relay's log is in the Flower run log: `FLWR_HOME=~/flhub-runtime/flower/flwr-home flwr log <run-id> flhub --show` (verified).
- **Per-message process cost.** `flwr-clientapp` starts a fresh process per message. For many rounds or big models, `--isolation process` with a long-lived SuperExec would avoid the start-up cost.

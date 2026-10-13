# Flower agent report (flower VM 145.38.187.20)

## 1. Scope and ownership

The Flower slice of AgnosticFL. The hospitals `flw-h5` and `flw-h6` are standard Flower SuperNodes on the flower VM. On the central VM, the hub talks to them through a Flower SuperLink, a relay ServerApp and the Flower adapter.

| Path | What |
|---|---|
| `clients/flower/` | Flower app (FAB): standard `ClientApp` (hospitals) and the hub relay `ServerApp` (central only) |
| `adapters/flower/service.py` | Flower adapter: flhub/0.1 on `127.0.0.1:8813` (`RelayAdapter`) + relay launcher |
| `deploy/central/flower/` | central VM: install, start, stop, status of SuperLink + adapter |
| `deploy/flower-vm/` | flower VM: install, start, stop, status of the two SuperNodes; `supernode.sh` restart loop; island config |
| `tests/flower/test_flower_relay.py` | relay + adapter tests with a fake `Grid` that runs the real ClientApp |
| `docs/DECISIONS.md` ADR-007 | Flower 1.39 deployment decisions |

## 2. What was built

One round, step by step (Flower part):

1. The orchestrator calls `dispatch` on the Flower adapter (`127.0.0.1:8813`). `RelayAdapter` queues a job: global model ref + `config` (dispatch `train_config` + `round`).
2. The relay ServerApp runs inside one long-lived Flower run on the central SuperLink. It long-polls `GET /relay/next` (`RelayClient.next_job`) and loads the global weights from the hub core (`RelayClient.get_model`).
3. The relay sends one standard TRAIN `Message` per site through the `Grid`. The content is a `RecordDict` with `"arrays"`, an `ArrayRecord` keyed by the PyTorch `state_dict` names, and `"config"`, a `ConfigRecord` holding the job config. Each message has `dst_node_id` = the site's SuperNode, `ttl` = the job's remaining deadline and `group_id` = round.
4. Each SuperNode pulls its message over the Fleet API (`145.38.205.58:9092`) and fetches the FAB from the SuperLink. It then runs the ClientApp's `@app.train()`, which reads the arrays and config, runs `flhub_core.train(weights, load_site(data-path), config)` and replies. The reply carries `"arrays"` (new weights), `"metrics"` (`MetricRecord` with `num-examples`, `train_loss`, `train_acc`) and `"site-info"` (`site-id`, `client-code-version`).
5. The relay pulls the replies and stores each site's weights in the hub core (`RelayClient.result`, a blob ref). It posts `/relay/progress` per reply and `/relay/result` at the end, or one `/relay/result` error naming the failed sites.
6. `RelayAdapter` builds the envelopes. `tasks/get` reports `completed` and `collect` returns them.

Key design choices:

- **Relay = a standard ServerApp in a long-lived run.** The adapter (`RelayLauncher`) keeps exactly one relay run alive. When no relay has polled or reported progress for 45 s, it submits `flwr run <repo>/clients/flower flhub --run-config "adapter-url=... hub-url=..."` against the SuperLink's local HTTP API (`127.0.0.1:9093`) and `flwr stop`s the runs it replaced. On stop it delivers a `shutdown` to the relay.
- **Node id → site id by a standard QUERY message.** The ServerApp cannot see a SuperNode's `--node-config`, and node ids are random on every start. The relay sends a QUERY to every new online node; the ClientApp's `@app.query()` answers with its `site-id`. Queries are non-blocking, so a dead node that is still listed cannot stall the loop. When two nodes claim one site (a restarted SuperNode whose old id has not expired yet), the node that answered last wins. Mapped sites are reported to the adapter (`/relay/sites`), which feeds `discover`.
- **Fail fast.** A site without a connected SuperNode fails the round at once. A destination node that goes offline mid-round fails it within one node refresh (5 s), instead of waiting for `deadline_s`. A cancelled or timed-out job is noticed through `/relay/status` within 10 s, and a 404 on a late result is ignored.
- **Bit-exact transport.** `ArrayRecord({name: Array(ndarray)})` serializes with `np.save` and keeps names and order (JSON refs), so float32 weights cross Flower unchanged. The conformance diff is 0.0.
- **Persistent SuperLink + supervised SuperNodes** (ADR-007). The SuperLink runs with `--database`, so SuperNode ids survive a SuperLink restart. Each `flower-supernode` runs under `deploy/flower-vm/supernode.sh`, which restarts it if it exits. A cron watchdog re-runs the idempotent `start.sh` every minute while the nodes are enabled.

## 3. Framework specifics (Flower 1.39.0, verified in the installed source)

APIs used:
- ServerApp: `flwr.serverapp.ServerApp`, `@app.main()` → `main(grid: Grid, context: Context)`.
- Grid: `Grid.get_node_ids()` (ONLINE nodes of the run's federation), `Grid.push_messages()`, `Grid.pull_messages()`.
- Messages: `flwr.app.Message(content, dst_node_id, MessageType.TRAIN|QUERY, ttl=, group_id=)`, `Message(content, reply_to=msg)`, `msg.has_error()`, `msg.error.reason`.
- Records: `RecordDict`, `ArrayRecord`, `Array(ndarray)`, `Array.numpy()`, `ConfigRecord`, `MetricRecord`.
- ClientApp: `flwr.clientapp.ClientApp` with `@app.train()` and `@app.query()`; `Context.node_config`, `Context.run_config`.
- CLI:
  - `flower-superlink --insecure --fleet-api-address 0.0.0.0:9092 --host 127.0.0.1 --port 9093 --database <file> --disable-runtime-dependency-installation`
  - `flower-supernode --insecure --superlink 145.38.205.58:9092 --port 9094 --node-config "site-id='flw-h5' data-path='/home/<user>/flhub-data/flw-h5.csv'"`
  - `flwr run <app-dir> <connection> --run-config ...`, `flwr stop <run-id> <connection>`, `flwr log <run-id> <connection>`
  - connections live in `$FLWR_HOME/config.toml` (`[superlink.flhub] address = "127.0.0.1:9093"`, `insecure = true`).

Surprising or underdocumented in 1.39 (compared with the plan, which assumed an older layout):
1. The ServerAppIo gRPC API (plan port 9091) is gone. `flwr run` (Control API) and the ServerApp (Runtime API, `HttpGrid`) share one SuperLink HTTP API on `--host/--port` (default `127.0.0.1:8000`). We run it on `127.0.0.1:9093`, so 9091 is unused. `--control-api-address` is deprecated.
2. `flower-supernode --clientappio-api-address` is removed (rejected at start). Each SuperNode has its own local Runtime API `--port`, so two SuperNodes on one host need distinct ports (9094/9095).
3. `flwr run` no longer reads `[tool.flwr.federations]` from the app's `pyproject.toml` (legacy, auto-migrated). Connections are named in `$FLWR_HOME/config.toml`, and `flwr run` returns right after submitting (prints the run id).
4. The SuperLink installs FAB dependencies at runtime by default (`uv sync` in SuperExec); SuperNodes do not. We disable it and preinstall.
5. `flower-superexec`, `flwr-serverapp` and `flwr-clientapp` are spawned by bare name via `PATH`, not with `sys.executable`. The venv's `bin` must be first on `PATH`.
6. `flwr-clientapp` is a fresh process per message (torch import each round, about 1–2 s).
7. A SuperNode whose node id is unknown to the SuperLink (in-memory SuperLink restarted) gets `UNAUTHENTICATED`, sends itself SIGINT and logs "SuperNode terminated gracefully", so it looks like a normal stop.
8. `Message(...)` outside a running app needs `TaskIdentity` (run/node/task id) set. The tests set it.
9. The FAB is limited to 10 MiB and includes only `*.py, *.toml, *.md, *.yaml, *.json, ...` (honours `.gitignore`). `flwr new` needs network access to the Flower Platform API.

## 4. Hospital-side footprint (flw-h5, flw-h6)

A hospital performs exactly this (`deploy/flower-vm/install.sh`, then `start.sh`):

1. Install: Python 3.11.17 venv with `flwr==1.39.0`, `torch==2.14.1+cpu` (CPU index), `numpy==2.4.4`, `safetensors==0.8.0` and `flhub_core` (the shared training library, installed from the repo). The hub package (`adapters`, `hub`) is not installed. The training data is one CSV per site in `~/flhub-data/`.
2. Configure and run the standard SuperNode. The only hub-specific settings are the SuperLink address and the node config:
   ```
   flower-supernode --insecure --superlink 145.38.205.58:9092 --port 9094 \
       --node-config "site-id='flw-h5' data-path='/home/<user>/flhub-data/flw-h5.csv'"
   ```
3. Training code arrives the standard Flower way: the FAB shipped by `flwr run` from the operator's SuperLink. The SuperNode loads only `[tool.flwr.app.components].clientapp` (`flhub_flower.client_app:app`, verified in `flwr/clientapp/utils.py`). That ClientApp is the documented Message-API pattern: `@app.train()` reads the `ArrayRecord` and `ConfigRecord`, trains and replies with an `ArrayRecord` and a `MetricRecord`. `@app.query()` returns the node's `site-id`. The relay ServerApp is in the same FAB but is never imported on a hospital.
4. Operations only, not Flower code: `supernode.sh` restarts `flower-supernode` if it exits, a cron watchdog re-runs `start.sh` while the nodes are enabled, and the processes run with a neutral cwd (`~/flhub-runtime/flower/<site>`).

Evidence: conformance passes with the SuperNodes in the clean hospital venv (`import adapters` fails there; flwr, torch, flhub_core only); `client_code_version` 0.1.0+9c6b064c1e96 in every envelope.

## 5. Timeline (UTC, from the bus)

| Time | Event |
|---|---|
| 12:00 | flower agent up (#4); hooks verified; `~/venvs/flower` (flwr 1.39.0, torch 2.14.1+cpu) |
| 12:04 | Flower 1.39 port-map change reported to central (#7): 9091 gone, 9093 = HTTP API |
| 12:11 | flower VM selfcheck bit-exact with central (#11) |
| 12:13 | first end-to-end round through a local SuperLink + 2 SuperNodes, weights == direct `flhub_core.train` (diff 0.0) |
| 12:14 | first push 661236f: FAB, relay ServerApp, adapter (#13) |
| 12:44 | 7f5adec: adapter/relay on `adapters.common`, deploy scripts, tests, ADR-007 (#44); **M1-flower** (#45); deploy-request (#46) |
| 12:46 | central deploy-result PASS (#49); 8b19ca5 status.sh exit codes (#47) |
| 12:47 | SuperNodes switched to 145.38.205.58:9092 (#51) |
| 12:50 | **M2-flower** by central (#54): conformance 7/7 diff 0.0; flower-only 3 rounds == sim |
| 12:51 | M2-mixed (#55); P3 freeze b656577 (#56) |
| 12:52 | d0ce5ef SuperNode watchdog (#57); freeze confirmed (#59) |
| TODO(verify) | M3-final, final-call, report |

## 6. Problems and fixes

| Problem | Component | Symptom | Root cause | Fix | Commit |
|---|---|---|---|---|---|
| Plan ports did not exist | SuperLink | no `--serverappio-api-address` / `--clientappio-api-address` | Flower 1.39 moved to one HTTP API per SuperLink/SuperNode | SuperLink `--host 127.0.0.1 --port 9093`; SuperNode `--port 9094/9095`; ADR-007 | 7f5adec |
| Hospitals vanish after a SuperLink restart | SuperNode | both SuperNodes logged "terminated gracefully" after `deploy/central/flower/stop.sh; start.sh` | in-memory SuperLink forgot the node ids → `UNAUTHENTICATED` → SuperNode SIGINTs itself | SuperLink `--database`; `supernode.sh` restart loop; tested both: same DB keeps ids, wiped DB → nodes back in ~10 s | 7f5adec |
| Stale node id after a SuperNode restart | relay | old id still listed ONLINE until its heartbeat expires | random node ids, heartbeat-based offline tagging | non-blocking site-info QUERY; newest node per site wins; old id ignored | ccc26dc |
| Relay loop could stall up to 60 s | relay | blocking query to a dead node → adapter sees relay stale → launches a second relay | `send_and_receive` style wait inside the poll loop | queries pushed once, replies collected on later refreshes | ccc26dc |
| Dead node mid-round waits for `deadline_s` (1800 s) | relay | round hangs | nobody checked node liveness during the wait | `_exchange` drops destinations that leave `get_node_ids()`; round fails in ~5 s | ccc26dc |
| Adapter not stopped by its pidfile | deploy | new adapter: "Address already in use" | `(cd ... && setsid nohup ... &)` put the background `&` on the AND-list, so `$!` was a wrapper subshell | `cd` at script level, background the command directly | 7f5adec |
| Relay considered dead during long rounds | adapter | (design review) `RelayAdapter` only tracks polls | relay does not poll while a round runs | `/relay/progress` every 10 s counts as relay contact | 998122e, ffb3cd8 |
| `pkill -f` killed the agent's own shell twice | ops | exit 144 | pattern also matched the shell's command line | only kill by pidfile or by listening port | — |

## 7. Tests and evidence

- `tests/flower/test_flower_relay.py` (5 tests, ~6 s): bit-exact round vs `flhub_core.train`; restarted SuperNode replaces a stale node; missing and lost sites fail fast; cancel then next round; relay liveness and shutdown. Run: `~/venvs/flower-central/bin/python -m pytest tests/flower`.
- Island (flower VM, scripts only), M1:
  - conformance 7/7, diff 0.0 in rounds 1/2/4 (`docs/report/evidence/conformance-flower-island.json`)
  - `hub.orchestrator` 3 rounds == `hub.sim`, diff 0.0 every round (`docs/report/evidence/equivalence-flower-island.json`)
- Central deployment, M2 (run by central):
  - conformance 7/7, diff 0.0 (`docs/report/evidence/conformance-flower-central.json`)
  - flower-p2 3 rounds == sim
  - mixed-p2 (6 sites) == sim, diff 0.0
- Final runs: TODO(verify) `flower-only` and `mixed-6sites` results.

## 8. Open issues / recommendations

- Security: Flower runs without TLS and without SuperNode authentication, restricted by the cloud firewall to the flower VM (ADR-007). Next step: SuperLink `--ssl-*` certificates, `--root-certificates` on the SuperNodes, and `--enable-supernode-auth` with per-hospital key pairs.
- The SuperLink logs every HTTP request (`uvicorn.access`); for long-lived deployments use `--log-file` with rotation.
- The relay's own log goes to the Flower run log: `FLWR_HOME=~/flhub-runtime/flower/flwr-home flwr log <run-id> flhub --show`.
- `flwr-clientapp` starts a fresh process per message; for large models or many rounds a warm process (`--isolation process`) would cut the ~1–2 s overhead per round.

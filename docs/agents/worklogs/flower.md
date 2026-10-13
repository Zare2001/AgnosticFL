# Worklog — flower agent

Newest entries at the bottom. Format: `### <UTC timestamp> — <phase> — <title>` + 2–6 bullets (what, result, evidence, next).

### 2026-10-13T12:20Z — P0 — bootstrap, hooks, venv, Flower 1.39 API study
- VM 145.38.187.20: Ubuntu 24.04.5, 4 vCPU, 31 GiB, uv 0.13.0. Bus OK; 9092 "connection refused" (firewall open, no listener yet).
- Hooks verified: scratch commit is Zare2001/Zare2001 with no trailer; commit-msg strips attribution lines, blocks 93-char subject and 5-line body.
- `~/venvs/flower`: Python 3.11.17, flwr 1.39.0, torch 2.14.1+cpu, numpy 2.4.6, safetensors 0.8.0 (log: ~/.flhub/logs/venv_flower.log).
- Flower 1.39 API summary (verified in installed source):
  1. SuperLink: `flower-superlink --insecure --fleet-api-address 0.0.0.0:9092 --host 127.0.0.1 --port 9093`. ONE HTTP API (`--host/--port`, default 127.0.0.1:8000) serves both the Control API (`flwr run`) and the Runtime API (ServerApp). No ServerAppIo :9091 anymore; `--control-api-address` deprecated.
  2. Isolation `subprocess` (default): SuperLink spawns `flower-superexec`, which spawns `flwr-serverapp` per run. Bare names on PATH, so the venv bin must be on PATH.
  3. SuperLink installs FAB deps at runtime by default -> use `--disable-runtime-dependency-installation`. SuperNodes do not install deps by default (FAB unzipped to `$FLWR_HOME/apps/...`, added to sys.path).
  4. SuperNode: `flower-supernode --insecure --superlink HOST:9092 --port 9094 --node-config "site-id='flw-h5' data-path='/x.csv'"`. Values are TOML (strings quoted). `--clientappio-api-address` removed; two nodes on one host need distinct `--port`. Node ids are fresh on each start (in-memory). Infinite reconnect retries by default.
  5. `flwr-clientapp` is spawned per message (fresh process, imports torch each time).
  6. `flwr run [APP] [CONNECTION]`: connection from `$FLWR_HOME/config.toml` (`[superlink.<name>] address=..., insecure=true`). It returns right after submit with the run id; `flwr ls/stop/log <run_id>`.
  7. FAB = `*.py, *.toml, *.md, ...` of the app dir (honours .gitignore), max 10 MiB. Required: `[project] name, version`, `[tool.flwr.app] publisher`, `[tool.flwr.app.components] serverapp, clientapp`.
  8. ServerApp: `@app.main() def main(grid, context)`. `grid` = HttpGrid: `get_node_ids()` (ONLINE nodes only), `push_messages`, `pull_messages`, `send_and_receive(msgs, timeout=)` (polls every 3 s).
  9. `Message(RecordDict, dst_node_id, MessageType.TRAIN, ttl=, group_id=)`; reply `Message(RecordDict, reply_to=msg)`; `msg.has_error()`, `msg.error`.
  10. ClientApp: `@app.train()`, `@app.evaluate()`, `@app.query()`; `context.node_config`, `context.run_config`, `context.node_id`.
  11. `ArrayRecord({name: Array(ndarray)})` keeps names and order (JSON refs); `Array` = np.save bytes (exact float32 round-trip); `.numpy()`.
  12. Limits: DEFAULT_TTL 12 h; gRPC max ~2 GiB; ServerApp task heartbeat every ~20 s from a thread; no run max-duration -> a long-lived relay run is fine.
- Next: wait for M0-core; meanwhile prototype the FAB (client + relay) and the adapter against a stub.

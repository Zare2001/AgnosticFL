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

### 2026-10-13T12:45Z — P1 — M1-flower island passes
- Built (island on the flower VM, started only via scripts): hub core :8800 (`python -m hub.core`), SuperLink (fleet 127.0.0.1:9092, HTTP API 127.0.0.1:9093, `--database`), adapter :8813 (`adapters/flower/service.py`, `RelayAdapter` + relay launcher), relay ServerApp (`clients/flower/flhub_flower/server_app.py`, `RelayClient`), SuperNodes flw-h5/flw-h6 (`deploy/flower-vm/*.sh`) in a clean hospital venv (flwr 1.39.0, torch 2.14.1+cpu, flhub_core only; cwd outside the repo).
- Conformance `pytest tests/conformance --adapter http://127.0.0.1:8813`: 7 passed; max_abs_diff 0.0 in rounds 1, 2, 4; n=784/784; code 0.1.0+9c6b064c1e96 (report /tmp/conformance-flower.json).
- `hub.orchestrator run --config deploy/flower-vm/island-3r.yaml --wandb offline`: finished, 3 rounds, 6-8 s/round; vs `hub.sim` same config: `equivalence_check` max abs weight diff 0.0 rounds 0-3, acc diff 0.0 pp -> PASS.
- Problems solved: (1) Flower 1.39 port/API changes (ADR-007); (2) SuperNodes die when an in-memory SuperLink restarts -> persistent `--database` + `supernode.sh` restart loop (tested: same DB keeps ids, wiped DB -> restart in ~10 s, rounds still bit-exact); (3) dead node id still listed online after a SuperNode restart -> non-blocking site-info QUERY, newest node wins; (4) node lost mid-round -> round fails within ~5 s instead of waiting deadline_s; (5) deploy pidfile bug (`cd && setsid ... &` recorded a wrapper pid) fixed.
- Next: commit + push, M1-flower milestone, deploy-request to central.

### 2026-10-13T12:53Z — P2 — M2-flower on the central VM
- deploy-request #46 (7f5adec, then 8b19ca5 for status.sh) -> central deploy-result #49 PASS: SuperLink fleet 0.0.0.0:9092 public, HTTP API 127.0.0.1:9093, adapter 127.0.0.1:8813, relay run polling; no secrets needed (insecure, ADR-007).
- Switched hospitals: stopped the island server side + local hub core, `deploy/flower-vm/start.sh` with the default SuperLink 145.38.205.58:9092 (only config change); both SuperNodes connected (status HEALTHY).
- Central evidence (#54, M2-flower): conformance vs :8813 7 passed, max_abs_diff 0.0 rounds 1/2/4, n=784/784; `--only flower --rounds 3` run == hub.sim (diff 0.0). M2-mixed (#55): 6 sites, 3 rounds, == sim (diff 0.0).
- Added: status.sh exits 1 unless both sites online (`--no-sites` opt-out, 8b19ca5); cron watchdog re-runs `deploy/flower-vm/start.sh` every minute while enabled (d0ce5ef; supervisor crash -> back in ~33 s).
- Next: confirm the frozen SHA for P3, keep SuperNodes up and untouched until M3-final, watch logs.

### 2026-10-13T13:12Z — P3/P4 — M3-final, report
- Final runs (central, #66, b656577, W&B tag final): flower-only 011lasv0 acc 0.7580 / AUC 0.9149 (221 s); mixed-6sites iehjnc44 acc 0.8654 / AUC 0.9247, 30/30 rounds, 6/6 sites each round; mixed == sim-fedavg-reference, max abs weight diff 0.0 rounds 0-30.
- SuperNodes untouched during the finals; no ERROR lines in either SuperNode log; status HEALTHY (13:10Z).
- Verified in an isolated throwaway SuperLink (ports 19092/19093/18813): `flwr log <run-id> <conn> --show` shows the relay's log.
- Wrote `docs/report/flower.md` (final-call #67): scope, design, Flower 1.39 specifics, hospital footprint with exact commands, timeline, problems table, evidence, reproduce steps.
- Next: report-ready to central; keep SuperNodes up until all-done; then verify DoD items, final entry, `touch ~/.flhub/DONE`.

### 2026-10-13T13:22Z — P4 — all-done: DoD items verified (flower)
- all-done #84 (1b66a28). Verified on the flower VM: DoD 1 `deploy/flower-vm/status.sh` HEALTHY exit 0 (flw-h5/flw-h6 connected to 145.38.205.58:9092, watchdog enabled); DoD 2 hospital venv = flwr 1.39.0 + torch 2.14.1+cpu + flhub_core only (`import adapters`/`import hub` fail), FAB ClientApp is the standard Message-API pattern; DoD 3/4/5 from central evidence (conformance 7/7 diff 0.0 vs :8813; mixed-6sites 30/30 rounds x 6 sites; equivalence diff 0.0); DoD 7 `git log` 83+ commits all Zare2001, no attribution; pytest green (tests/flower 5 passed where flwr is installed, skipped otherwise); DoD 8 docs/report/flower.md pushed.
- REPORT.md §5 Flower row made exact (`--insecure`, `--port`, TOML-quoted node config): 0cac28f.
- Cleanup: offline W&B dirs of island dev runs, island hub core data, island SuperLink state and the island-only venvs (~/venvs/flower-central, ~/venvs/hub) removed; the VM now holds only the hospital side (~/venvs/flower, ~/flhub-data/flw-h5.csv, flw-h6.csv, ~/flhub-runtime/flower) + the repo checkout.
- SuperNodes keep running (watchdog enabled). `~/.flhub/DONE` created.

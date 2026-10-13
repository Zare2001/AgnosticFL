# Stage 2 — vantage6 agent (145.38.186.63)

Draft during S4; the experiment numbers in §5 are filled in from the final runs (`docs/report/stage2/runs.md`).

## 1. Scope

- The two vantage6 hospitals `v6-h3` and `v6-h4` (standard vantage6 4.15.2 nodes, Docker) and, for E5, `v6-h9`/`v6-h10`.
- The vantage6 adapter on flhub/0.2 (`adapters/vantage6`), its central deployment (`deploy/central/vantage6`) and the hospital scripts (`deploy/vantage6-vm`).
- The rollout of `flhub_core` 0.2 the vantage6 way, the payload limits of vantage6, the E6 fault-injection tools on the vantage6 VM, and the E7 native FedAvg baseline.

## 2. What changed

- Adapter (ADR-013): one vantage6 task per site and round. Per-site state comes from `task/<id>/status`, per-site results are reported as soon as a site's task completes, a site is cancelled with `task.kill`, a failed site leaves the others alone. Separate client sessions for control calls and for each site's data; control calls give up after 2 connection errors, so a server outage only delays a round and `discover` answers `unknown`. The data variant hint selects the node database (`db_label`). Status polling slows down with the model size in DB mode; vantage6 tasks of models >= 16 MB are deleted after the round. `needs_weights = False`; per-site `vantage6_{create,fetch,store}_s` in the envelope metrics.
- Central scripts: `add_sites.sh` (E5 organisations and nodes through the API), `restart_server.sh` (E6e), `restart_adapter.sh` (conformance, E6d), `purge.sh [--vacuum]` (E4a), `native/` (E7), overrides for a development island (`V6_RT`, `V6_VENV`, `V6_SERVER_BIND`); extra sites listed in the adapter, health = base sites.
- Hospital scripts: node configs with one database per data variant and `share_config: false` (ADR-014), `taskdir_gc.sh` (cron), `extra.sh` (E5), `inject.sh` and `straggler.sh` (E6, ADR-015), `netem.sh` (E4b), `sysmon.sh` and `run_follow.py` (S4 monitoring); `island.sh` and `island_round.py` for development only.
- ADRs: 013 (adapter), 014 (data variants), 015 (E6 tools), 020 (payload path), 021 (rollout), 022 (E7 native).
- Ledger rows: data provisioning, node config switch, task-folder GC cron (`docs/report/stage2/ledger.md`).
- `git diff --stat stage1-final..HEAD -- clients/vantage6`: empty. For all vantage6 directories (at c2871ea): 27 files, +1589/-145 (adapter 304, native 226+165, hospital scripts ~840, central scripts ~250).

## 3. Rollout of flhub_core 0.2

vantage6 ships training code as an algorithm image that nodes pull per task (ADR-021).

| step | who | what | time |
|---|---|---|---|
| 1 | hospital (once, data) | provision the 9 variant CSVs per site (central's `materialize`, sha256 against the manifests) | 14:28Z, < 1 min |
| 2 | hospital (once, config) | node config: 10 databases (`default` + one label per variant) and `share_config: false`; 1 restart per node | 13.2 s install, 14.7 s offline (15:36Z) |
| 3 | central | `install.sh`: build + push `v6-train:a43cef3f75c0` (`clients/vantage6` + `flhub_core` 0.2), adapter to the new tag | 98 s (15:42:37-15:44:15Z) |
| 4 | central | `start.sh`: restart only the adapter | 2 s |
| 5 | hospital (optional) | pre-pull the new tag (otherwise pulled at the first task) | 12.6 s |

- Code update cost for a hospital: 0 steps, 0 restarts, 0 downtime (steps 1-2 are the one-time stage-2 data/config change, not needed for later code updates).
- Selfcheck v2 inside the pulled image with `--network none`: digest `sha256:ac074a0b...` = central; `code_version` 0.2.0+fe357a1dbec1.
- Conformance v2 against central: 13/13, max abs diff 0.0 in every round (`docs/report/stage2/evidence/conformance-v2-vantage6-central.json`).
- What a real hospital would do: nothing for a code update (its node policy already admits `flhub/v6-train:<tag>`); for new data it adds a database entry to the node config and restarts the node once.

## 4. Limits and knobs (E4)

Evidence: vantage6 4.15.2 source (server/node wheels, client, algorithm tools) and island measurements (ADR-020).

- **Encoding**: task input and results are stored per organisation as base64(json(...)); our weights are already base64 inside the JSON, so each run's input and result take ~1.78x the model in the server DB (sqlite `Text`).
- **Hard limit**: sqlite in the server image rejects a value > 1e9 bytes (measured: 999,999,000 ok, 1,000,000,001 rejected) -> ~536 MiB is the largest payload DB mode can store; 1024 MB cannot be carried.
- **Scaling**: the node streams every algorithm log line to the server, which loads the run row and saves it; sqlite rewrites the whole row with its ~1.78x input for each line (py-spy: `on_algorithm_log` -> `run.save()`). About 24 lines per round block the single gevent worker for ~60 s at 64 MB (round 117-123 s instead of ~8 s); the cost grows with payload x log lines. Status polls also load full run rows.
- **Hospital disk**: nodes never delete `task-<run_id>` folders (~2.7x the model per site and round) -> `taskdir_gc.sh` (cron, ledger).
- **Knobs**: none usable server-side in 4.15.2. The large result store (`large_result_store`, Azure Blob/Azurite) fails: the client streams 1 MiB chunks, which the server's uWSGI rejects at its default 1 MiB chunked-input limit, and the sqlite-backed server deadlocks while it streams blobs. Would help: hospital-side `share_algorithm_logs: false` (recorded, not applied), PostgreSQL as server DB (TOAST keeps the input out of row rewrites).
- Final E4a numbers per size: see §5.

## 5. Experiments supported

- E4a vantage6 scope (DB mode, 3 rounds, median of rounds 2-3, central RUN end messages #297-#331):

  | payload | median round | vantage6 VM mem peak | largest process RSS |
  |---|---|---|---|
  | 1 MB | 6.3 s | — | — |
  | 8 MB | 12.8 s | — | — |
  | 32 MB | 60.1 s | — | — |
  | 64 MB | 115.3 s | 3.3 GB | training containers 1.4 GB, node 1.0 GB |
  | 128 MB | 468.2 s (r1 536 s) | 5.3 GB | training containers 2.1 GB, node 1.6 GB |
  | 256 MB | testbed disk limit on central (measured projection: sqlite 1.14 GB + 0.20 GB journal at 128 MB -> ~4 GB at 256 MB, below the 3 GB floor) | | |
  | 512 MB | testbed disk limit on central | | |
  | 1024 MB | not storable: sqlite value limit 1e9 bytes < 1.78 x 1024 MiB | | |

  Superlinear above 64 MB: the server is one uWSGI process at 100 % CPU rewriting the run rows per algorithm log line; the nodes log `Connection error... Retrying` (RemoteDisconnected from uWSGI while the worker is blocked) and the vantage6 client retries; every round completed. RAM on the vantage6 VM stays < 7 % per process.

  Where a vantage6 site's time goes (median of rounds 2-3, `site/<id>/*` in the run dirs from central, bus file #72): at 128 MB t_result 467 s = training 0.05 s + adapter 33-38 s (`vantage6_create_s` 10-11 s: input encoding and upload; `vantage6_fetch_s` 15-21 s: result download and decode; `vantage6_store_s` 1-2 s: hub-core store) + ~430 s inside vantage6 (server DB rows, node download/upload, log streaming). At 64 MB: 115 s = 0.03 + ~13-17 + ~100 s. Training-container peak RSS (`peak_rss_mb`): 303 MB at 1 MB, 573 MB at 64 MB, 851 MB at 128 MB.
- E4a mixed scope (bus file #73): vantage6 sets the round time (`adapter/vantage6/fanout_s` = round duration): 1 MB 6.5 s, 8 MB 13.3 s, 32 MB 58.9 s, 64 MB 116.2 s; 128 MB _(to be filled)_; 256 MB testbed disk limit.
- E4b: `netem.sh` (egress to central 7601/5050 only; tested under RUNLOCK: connect time 0.73 -> 20.8 ms, bus untouched, cleared to fq_codel): _(results)_.
- E5: `extra.sh` for v6-h9/v6-h10 (configs installed, keys from central's `add_sites.sh`): _(results)_.
- E6 (island-tested; final results to be filled):
  - `kill` (SIGKILL the training container at round N + S s): run `crashed`, adapter reports the site failed ~3 s later; the node stays up.
  - `restart` mid-round: the node re-runs the open run after reconnecting; the round completes without a retry (19.6 s vs 8 s).
  - `straggle:10` rounds N..M-1 (`docker update --cpus 0.10` per training container): 44 s vs 11.5 s per round.
  - Server restart (E6e, `restart_server.sh`): rounds complete without a retry (29-48 s with 0-20 s extra downtime); `discover` answers `unknown` within 2.2 s during the outage.
- E7 native (ADR-022): central function `v6_native.fedavg` on the hub-operator node; island C1 hashes equal to the hub run in every round (8.5 s vs 8.1-8.8 s per round); final comparison _(to be filled)_.

## 6. Problems and fixes

| problem | symptom | root cause | fix | SHA | time lost |
|---|---|---|---|---|---|
| ADR number clash | push rejected by rebase conflict | other agents took ADR-010..012 meanwhile | renumbered to 013-015 | 399c12d | 10 min |
| adapter logs missing | no adapter INFO lines, unformatted vantage6 lines | parallel `UserClient()` logins replace the root logger | lock around the login | b08ae61 | 10 min |
| payload rounds ~120 s at 64 MB | server silent ~60 s per round | per-log-line run-row rewrite in sqlite (DB mode) | none in 4.15.2 (result, ADR-020); polling adapted | 0ab8374 | 60 min |
| large result store unusable | blob upload 500, then server hang | client 1 MiB chunks vs uWSGI limit; sqlite deadlock while streaming | dropped (ADR-020) | — | 45 min |
| island server hung after node restarts | `on_node_info_update` busy for minutes | 785 column rows per CSV database deleted one commit at a time | `share_config: false` (ADR-014) | 525b7c5 | 30 min |
| island registry lost images | image missing after `island.sh up` | install.sh replaced a stopped registry container | restart it instead | d7be8ba | 5 min |
| VM disk < 3 GB twice | df 2.5 GB / 2.0 GB | island images, build cache, payload task folders | prune, GC cron, island removal | 2e2061b | 15 min |
| deploy-request late | central waited for vantage6 (#168) | the payload investigation above | — | — | ~40 min schedule slip |

## 7. Recommendations

- For large models with vantage6 4.15: run the server on PostgreSQL and set `share_algorithm_logs: false` on the nodes, or keep payloads small; the large result store needs the uWSGI chunk-limit fix and a non-sqlite DB.
- Nodes with many CSV databases: set `share_config: false` (or use a smaller column list); otherwise every reconnect blocks the server.
- Add a cleanup of finished task folders to the node (or a cron like `taskdir_gc.sh`).

# Stage 2 worklog — nvflare agent

Newest entries at the bottom. Format: `### <UTC timestamp> — S<n> — <title>` + 2–6 bullets (what, result, evidence, next).

### 2026-10-13T13:52Z — S0 — kickoff: health, disk, hospital venv pinned
- Read PLAN.md, EXPERIMENTS.md, roles/NVFLARE.md, stage-1 worklog tail. `deploy/nvflare-vm/status.sh`: nvf-h1/nvf-h2 up, connected to flhub-central:8002 = 145.38.205.58, 1 job process each, HEALTHY (exit 0). Disk 14G free (uv cache pruned, nothing else to clean); RAM 29G available. Bus info #99.
- Finding: the hospital venv had `flhub_core` as an EDITABLE install (`__editable__.flhub_core-0.1.0.pth` -> /home/zpalanciya/AgnosticFL/flhub_core/src). A `git pull` on this VM would silently change hospital training code at the next job start (stage-1 artifact).
- Fix (ops, ledger row): reinstalled `flhub_core` from `git archive stage1-final flhub_core` as a normal (non-editable) install; `install.sh` does the same via `FLHUB_CORE_REF` (default stage1-final). Same code_version 0.1.0+9c6b064c1e96, selfcheck result sha256:7c80a5a3777915ff… (= stage 1). Clients not restarted, still HEALTHY.
- Next (S1): payload research, custom-code import order (rollout ADR), dynamic provisioning for nvf-h7/h8, native FedAvg (E7), inject/straggler/netem tools, purge.sh; flhub/0.2 adapter once central specifies it.

### 2026-10-13T14:35Z — S1 — research, per-site watchdog, flhub/0.2 adapter on the island
- Research (scouts + own reading, NVFlare 2.9.0 source): (1) job `custom/` is APPENDED to sys.path (`utils/job_launcher_utils.py:486-503`; `runpy.run_path` adds nothing), so a flhub_core installed in the hospital venv shadows a copy shipped in the job -> rollout plan: ship flhub_core 0.2 in the job's custom/ and remove the venv copy once (ADR to follow with the rollout). (2) numpy params travel via the DownloadService in 2 MiB chunks (ViaDownloaderDecomposer), not inline: the 2 GiB frame cap does not bound the model size; knobs = comm_config.json streaming_* and timeouts (payload table follows). (3) `nvflare provision --add-client` reuses state/cert.json (same root CA) and writes prod_NN+1; our provision.sh --force would wipe the CA -> E5 kits need a separate script. (4) FedAvg/WeightedAggregationHelper aggregate in float32 in arrival order (E7: expect ulp-level native-vs-hub differences).
- Watchdog per site (a996bab, ledger): stage-1 `stop.sh <site>` disabled the watchdog of all sites; now `.enabled` markers + one cron line per sites dir; real clients migrated without restart; tested with a dummy kit (cron restart after kill, stop removes only its line).
- flhub/0.2 adapter (ADR-011): relay = one NVFlare task per site via `Controller.send` (no per-task deep copy), per-site posts, `/relay/job` polling -> cancel; adapter fails offline sites alone, fails a site offline for 30 s mid-round, relay job min_clients 1 with submit gating; central scripts get NVF_BASE_SITES/NVF_EXTRA_SITES.
- Island on this VM (~/flhub-island: localhost server, hub core, adapter :8811, island kits for nvf-h1/h2 run with the hospital venv; real clients untouched): unit tests 11 passed; conformance v2 11 passed, diff 0.0 in every round, incl. subset, partial collect + site cancel + next dispatch (3.2 s), offline extra nvf-h7, server+adapter restart (online again after 53.9 s).
- Next: inject.sh/straggler.sh tests on the island, payload knobs table, native FedAvg (E7), provision_extra.sh (E5), purge.sh, netem.sh; rollout ADR once central's S1-core SHA exists.

### 2026-10-13T14:40Z — S1 — E6 tools (inject.sh, straggler.sh) tested on the island
- `inject.sh arm <site> kill|kill-job|restart --round N --after S` + `inject_watch.py` (stdlib watcher, follows job logs, round N = first task after `[flhub] <site>: round N-1`), `disarm`, `status`; announces `E6 inject …` (NVF_INJECT_BUS=true for island tests); records recovery in `<inject dir>/<site>.result.json`.
- `straggler.sh on|off|status` via a transient system scope with CPUQuota (sudo busctl StartTransientUnit PIDs=client+job), no client restart.
- Island results (evidence file e6-tools-island.md, ADR-012): kill r3 -> site failed alone after 35 s, client up 4 s / registered 6 s / back in a relay job 62 s, strict retry OK; kill-job -> NVFlare 2.9.0 fails the WHOLE relay job at once (fed_server.py:944-951, no knob); restart via restart.fl acts after ~10 s; straggler 10 % -> 6.5-7.5 s vs ~2 s rounds, back to 2 s after off.
- Finding for E6/orchestrator: one relay job serves both NVFlare sites, so a site failure means a relay-job restart that takes the other NVFlare site out for ~15-30 s; retries should wait for discover to show the round's sites online.

### 2026-10-13T14:45Z — S1 — rollout channel proven on the island (ADR-018)
- `adapters/nvflare/job.py`: relay job ships custom/client.py + custom/flhub_core/ (.py of the checkout); export runs inside the script dir (NVFlare resolves the relative script at export time; first attempt silently dropped client.py) and fails if either is missing.
- Island switched like the hospitals will be: clients on ~/venvs/island-hosp (no flhub_core), data variants materialized with the repo's flhub_core (42 s, 17 variant files/site, manifest-checked) into ~/flhub-island/data next to the stage-1 CSVs.
- Clients report flhub_core 0.2.0+fe357a1dbec1 from the job; conformance v2 with --variant organamnist-dir0.3: 13 passed, diff 0.0 (payload-64 echo 3.5 s, variant round 3.5 s, restart -> online 40.9 s).
- Real hospital switch (S2, after central deploys the S1-core relay job): data provisioning first (no downtime), then uninstall flhub_core + client restart (~60 s downtime incl. relay-job restart).

### 2026-10-13T14:55Z — S1/S2 — E7 native FedAvg (ADR-019), sysmon running
- `adapters/nvflare/native.py` (NativeFedAvg + prepare/submit/finalize/compare) and `deploy/central/nvflare/native/run.sh`; `job.py` exporter takes per-site targets. Island: cnn-small organamnist-iid 3 rounds FINISHED:COMPLETED; clients == local train (0.0); hub fedavg vs NVFlare FedAvg on the same client results 3-6e-8 (float32 InTime vs float64); payload-8 via run.sh OK (evidence e7-native-island.md).
- Problems solved: FedAvg drops meta after round 1 (re-attach train_config in send_model); server app without custom code -> ComponentNotAuthorized (ship workflow as BYOC server code); `python -m` builder serialized `__main__` class path (build from package import).
- S1-core milestone 894f7b7 received (bus #129). sysmon started via `deploy/nvflare-vm/sysmon.sh` (per-site client/job components, island grouped apart).
- Next: purge.sh, island payload check (128-512 MB), data provisioning on nvf-h1/h2, ONE deploy-request.

### 2026-10-13T15:06Z — S2 — rollout prepared and rehearsed; data provisioned; E5 path tested
- Real hospitals: data variants provisioned (9 files/base site + iid12 for h7/h8, manifest-verified, 12.9 s, no restart; ledger row). sysmon running.
- Island payload check (relay path, default NVFlare 2.9.0 knobs): payload-128 5-6 s/round, 256 9-10 s, 512 16-17 s (VM peak +5.3 GB), 1024 32.8 s (peak 17.6 GB used): no server-side knob needed for E4a; memory is the thing to watch on central.
- Shadowing confirmed empirically: island clients whose venv has flhub_core 0.1 ran 0.1.0+9c6b064c1e96 although the job shipped 0.2.
- `switch_s2.sh` rehearsed on the island in the real hospitals' state (editable 0.1, then pinned copy, stale path in the running clients): data check 12 s, uninstall, stop/start, both sites run the job's 0.2.0+fe357a1dbec1 after 87.5 s; downtime 59 s per site.
- E5: `provision_extra.sh` (nvflare provision --add-client; same root CA; prod_01/prod_02; no server restart) + `extra.sh install|start|stop|status`; island: 4 sites incl. nvf-h7/h8 run organamnist-iid12 rounds (3 s); `extra.sh stop` leaves the base sites' watchdog on. `purge.sh` deletes finished jobs via the FLARE API (island: 10 jobs) and stale server workspaces.
- Note: an exception in client.py makes NVFlare abort the whole relay job ("ask to abort job"); the keeper resubmits it.

### 2026-10-13T15:12Z — S1/S2 — NVFlare 2.9.0 payload limits and knobs (E4 research, source-verified)
How a model travels: the relay's FLModel params (numpy) are serialized with FOBS; `NumpyArrayDecomposer` is a `ViaDownloaderDecomposer`, so every array goes out-of-band through the sender's DownloadService in chunks (not inside the gRPC message). Island relay path with defaults: 128/256/512/1024 MB pass (5/10/17/33 s per round, localhost); no knob needed so far.

| knob | where (file:line) | set in | side | default |
|---|---|---|---|---|
| np download chunk size (`np_download_chunk_size`, env `NVFLARE_NP_DOWNLOAD_CHUNK_SIZE`; <= 0 = inline FOBS) | fuel/utils/fobs/decomposers/via_downloader.py:482, app_common/decomposers/numpy_decomposers.py:63-75 | job/app config var | sender (server for the global model, client for results) | 2 MiB |
| download transaction lifetime (`MSG_ROOT_TTL` = task timeout) | apis/impl/wf_comm_server.py:367; freed when the task leaves (`_release_task_resources`) | relay task timeout = dispatch `deadline_s` | server | deadline |
| CellNet `max_message_size` (inline messages only) | fuel/f3/comm_config.py:21,83 | `comm_config.json` / `.json.default` in the site workspace | both | MAX_PAYLOAD_SIZE = 2 GiB - 2 MiB - 1 MiB - 16 B |
| gRPC frame (`MAX_FRAME_SIZE`, max send/recv length) | fuel/f3/drivers/net_utils.py:40, grpc_driver.py:43-44 | code constant | both | 2 GiB - 2 MiB |
| `streaming_chunk_size` / `streaming_window_size` / `streaming_ack_interval` | fuel/f3/streaming/stream_const.py:23-25 | comm_config.json | both | 1 MiB / 64 MiB / 16 MiB |
| `streaming_max_blob_size` (env `NVFLARE_STREAMING_MAX_BLOB_SIZE`) | fuel/f3/comm_config.py:22,115 | comm_config.json | both | 4 GiB |
| `streaming_read_timeout` / `streaming_ack_wait` | fuel/f3/streaming/byte_receiver.py:54, byte_streamer.py:57 | comm_config.json | both | 300 s / 300 s |
| `get_task_timeout`, `submit_task_result_timeout`, `communication_timeout` | private/fed/client/client_runner.py:163-164, fed_client_base.py:94 | client job config / client args | hospital | None, None, 300 s |
| broadcast deep copy of task data (one copy per broadcast task) | apis/impl/wf_comm_server.py:308-314 | code | server | relay avoids it: `Controller.send()` per site |
| `memory_gc_rounds` | job_config/script_runner.py:92, client_api_executor.py:89 | client job config (ScriptRunner) | hospital (job) | 0 (off) |
| `MALLOC_ARENA_MAX`, `NVFLARE_ENABLE_JEMALLOC_PRELOAD` | kit startup/sub_start.sh | kit env | both | 4, false |
| job store (`uri_root`) | kit local/resources.json.default, job_manager | server kit | server | /tmp/nvflare/jobs-storage (purge.sh deletes finished jobs) |

Server-side candidates if E4a hits a limit on central: comm_config.json `streaming_read_timeout`/`streaming_ack_wait` (slow links), `streaming_max_blob_size` (> 4 GiB), job-level `np_download_chunk_size` (larger chunks = fewer round trips); hospital-side knobs (client timeouts, memory_gc_rounds) are recorded, not changed.

### 2026-10-13T15:25Z — S2 — E6e restart_server.sh; keeper fix for stale jobs after a server restart
- `deploy/central/nvflare/restart_server.sh`: restarts only the NVFlare server (kit shutdown.fl + start.sh), returns when 8002/8003 accept again (~14-16 s; the kit daemon acts after its 5 s poll + 5 s grace).
- Bug found on the island (two restarts ~16 s apart, a round in flight): the job store kept the old relay job as RUNNING while the restarted server did not run it; abort -> "not running", the keeper retried the abort forever and never resubmitted (sites offline > 5 min). Fix: `end_job()` treats "not running" as stale -> delete (NVFlare then answers "still running") -> ignore it and submit a new job; and the keeper ends a job at once when the relay has been silent for 30 s instead of waiting the 60 s shutdown grace (relay contact = polls, job-status polls, site reports). Unit test added (12 passed).
- Rerun: round 7 in flight during the restarts failed after 46 s (sites offline 30 s), keeper replaced the stale job, strict retry waited 38.5 s for online and completed; round 8 normal. Central's orchestrator v2 waits for online before retries (run.py:168,181).
- inject.sh: actions straggler-on:<pct> / straggler-off (E6b: on at ARM, off at the site's round-7 task via the log trigger).

### 2026-10-13T15:30Z — S2 — rollout on the real hospitals done; netem tested; E5 kits installed
- Central deployed f76942c (bus #148 PASS: relay job 90f69086 with custom/flhub_core, adapter flhub/0.2, nvf-h7/h8 registered offline, E5 kits from prod_01/prod_02). Real clients then still ran 0.1.0+9c6b064c1e96 (venv shadowing confirmed on the real system).
- switch_s2.sh 15:25:11-15:26:34Z: data verified 12 s, venv flhub_core removed, 1 client restart each; both sites in relay job 98362726 running 0.2.0+fe357a1dbec1 (82.6 s total, 50.3 s offline/site). Selfcheck v2 in the hospital runtime (venv python + job custom/): digest ac074a0b... at nvf-h1 and nvf-h2 (= central). Bus #161.
- RQ2 NVFlare rollout cost: hospital steps 3 (data provisioning 12.9 s, no downtime; uninstall + restart, scripted, 82.6 s, 50.3 s downtime/site); lines changed in clients/nvflare: 0; future flhub_core updates: 0 hospital steps (new relay job).
- netem.sh test under RUNLOCK #149: wan1g 15:24:16Z, revert timer first, 190 pkts on the netem band (dst :8002 only), cleared 15:24:57Z, fq_codel back, 0 timers; released (#158).
- E5 kits installed (extra.sh install; archives deleted); data iid12 present; sites stay down until E5.
- Replied to central #136 (E6 schedule: e6a kill --after 0.5; e6b straggler on at ARM + straggler-off at round 7; e6e restart_server.sh) and #156 (adapter-only restart = start.sh). Asked central to pull 05e2b2a + stop/start before conformance v2.

### 2026-10-13T15:35Z — S2 — S2-nvflare (central #165); E7 config integration
- S2 S2-nvflare (bus #165): conformance v2 vs central 13/13, diff 0.0 every round (subset, partial + site cancel + next dispatch 2.7 s, offline extras, restart -> online 30 s, payload-64, organamnist-dir0.3); evidence docs/report/stage2/evidence/conformance-v2-nvflare-central.json.
- native.py now loads the E7 hub config with central's loader (`hub.orchestrator.config`); run named s2-e7-nvflare-native-*; `--rounds` for dry runs only (24b4beb). Island dry run with configs/stage2/e7/s2-e7-nvflare-hub-cnn.yaml, 3 rounds, vs hub orchestrator v2 (`--only nvflare`): native-vs-hub r1 3.0e-8 (aggregation only), r2 6.3e-4, r3 3.4e-2 (SGD amplifies the float32 rounding; one-step aggregation diff <= 6e-8 each round). Evidence e7-native-island.md.
- Code freeze for NVFlare: since the deployed 05e2b2a only native.py/native/run.sh changed (E7-only, built from central's checkout at run time). Rollout SHA for the S3-frozen check: 24b4beb.
- sysmon.sh send <run> <from> <to>: summary window -> tools/bus put to central (per-site client/job components).

### 2026-10-13T15:50Z — S2 — relay timings per site result (for RQ4/E4a)
- The relay adds `nvflare_relay_prep_s` (global model from the hub core into an FLModel), `nvflare_relay_reply_s` (task sent -> reply at the server: NVFlare transport both ways + client work) and `nvflare_relay_store_s` (reply weights into the hub core) to each site's metrics (-> metrics.jsonl site/<id>/...). Weights unchanged. Island payload-128: prep 0.37 s, reply 3.8 s (client echo 0.05 s), store 0.5 s, round 5.65 s.

### 2026-10-13T15:55Z — S2 — ready for the freeze; island stopped
- Central restarted the NVFlare adapter at 3212150 (#190, HEALTHY after 10 s): relay timings + central's needs_weights=False. Told central NVFlare is final (#194); rollout SHA for the freeze check: 3212150.
- run_follow.py running (per RUN end: sysmon summary to central, rounds trained, flagged log lines, health); sysmon restarted with an island pattern that cannot match its own command line (it had counted itself as "island").
- Island processes stopped (clients, server, adapter, hub core; island cron line removed); files kept (~/flhub-island, venvs island/island-hosp/island-s1) for reproductions; to be removed in S5.

### 2026-10-13T16:02Z — S3 — confirmed S3-frozen 96c44c9
- S2-mixed (#200): 3 rounds, 6 sites, fedadam, hash-equal to sim; NVFlare fan-out 2.3-4.6 s, rounds 8.6 s.
- S3-frozen 96c44c9 (#210): git diff 3212150..96c44c9 empty for flhub_core, clients/nvflare, adapters/nvflare, adapters/common, deploy/central/nvflare; nvf-h1/h2 in relay job da5a973a with 0.2.0+fe357a1dbec1; HEALTHY, sysmon + run_follow running. Confirmed (#211-ish). Next: S4, E4a nvflare scope first; watch RAM (70 % rule).

### 2026-10-13T17:20Z — S4 — E4a done for NVFlare; breakdown and the 2 s latency floor
- Final runs (runs.md, INV-1 full-sim 0.000): nvflare scope 1/8/32/64/128/256 MB median round 2.52/2.67/2.66/3.67/5.08/10.29 s; 512 MB = central testbed disk limit (#250). Mixed scope 1..64 MB done, 128 MB disk limit (#373); NVFlare fan-out there 1.8-3.8 s, rounds set by vantage6.
- VM RAM peaks (follower summaries): 256 MB -> 3.48 GB VM, 1.35 GB per client job (70 % rule far away); wire/payload ~1.06 (#254).
- Breakdown (evidence e4a-breakdown.md, script deploy/nvflare-vm/analysis/e4a_breakdown.py on central's run data, bus files #70/#71): small payloads are dominated by a fixed ~2.3 s NVFlare floor; 256 MB: transfer 6.3 s, hub prep 0.67 s + store 1.0 s.
- Floor cause (source-verified): server tells clients to wait `task_request_interval` (default 2 s, server_json_config.py:102) before the next task request; client log: 0.55 s at the client per round, 2.04 s until the next task. Server-side latency knob; frozen, so unchanged; affects native E7 equally.

### 2026-10-13T21:50Z — S4 — E1/E2 monitored; E6a/E6b injections done
- E1 (7 real) and E2 (15 real) runs: NVFlare clean in every run (follower: 50 rounds/site, no flagged lines, HEALTHY; sysmon summary per run). Follower fixes: offsets from the previous poll (late RUN start), window bounded by RUN start/end in local log time (next run's first round no longer counted).
- s2-e6a-strict: armed `inject.sh arm nvf-h1 kill --round 5 --after 0.5` (#548); fired 21:25:58.181Z (round 5 at nvf-h1 21:25:57.49Z); client up 3.0 s, registered 5.0 s, back in a relay job 55.6 s; rounds trained nvf-h1 22, nvf-h2 23 (strict retries r5/r10/r15); 5 expected END_RUN/abort lines at nvf-h2 (relay-job restart).
- s2-e6a-minsites: same arm (#567); fired 21:31:58.569Z; client up 3.0 s, registered 4.0 s, back in a relay job only 97.2 s later: the keeper restarts the relay job only when no round is in flight, and with min_sites the hub dispatched back-to-back (nvf-h1 'not online' r6-r9) until the failed r10 attempt made it wait (central #574). Design trade-off of ADR-011, reported as NVFlare min_sites behaviour.
- s2-e6b-straggler: straggler.sh on nvf-h2 10 at ARM (#588) + straggler-off at nvf-h2's round-7 task (fired 21:40:52.214Z); nvf-h2 rounds 1-6 ~12.7 s (< deadline 20 s, never dropped); 20/20 rounds both sites; HEALTHY (#603).
- Flower's cron-watchdog lock issue (#592) checked here: lock free, watchdog active.

### 2026-10-14T06:30Z — S4 — E6c-E6e, E3, E5, E7 (first part) on the nvflare side
- E6d: relay job re-attached to the SIGKILLed+restarted adapter; 51 rounds/site (r21 redone). E6e: clients reconnected ~20 s after each server restart; keeper ran new relay jobs ~70 s / ~90 s after them; 21 rounds/site; flagged lines = expected disconnects. S4-E6 milestone #656 (detected 33.1 s / recovered 74.0 s for the nvf-h1 kill; minsites rejoin waits for a hub pause).
- E3 (#731): NVFlare per-site overhead 1.9 s (cnn-medium) / 2.7 s (resnet18-gn), lowest of the three; resnet18-gn training ~215 s/round/site, job RSS ~1 GB.
- E5: extras up on request (#734) 06:08Z, in relay job de332656 after 42 s, trained n=2880 (iid12) all 20 rounds; down on request (#747) 06:13Z, base relay job kept running; replies #744/#757.
- E7: native payload run on central OK (5 rounds, ~3 s apart, next to the relay job); native cnn did not start (central runner pre-created the out dir, run.sh refuses existing dirs; central fix d18485a, re-run later). Asked central to run `native compare` on central and send run data (#776).
- Finding (#777/#778): job store keeps relay jobs that were running at an abrupt server stop as RUNNING forever (ba97e23f, 83d02fb9 from E6e): NVFlare 2.9.0 defines JobRunner.update_abnormal_finished_jobs()/update_unfinished_jobs() but never calls them; abort -> 'not running', delete -> 'still running'. Harmless (not scheduled, keeper ignores them); documented.

### 2026-10-14T09:04Z — S5 — final-call: report section final, ledger complete, VM end state clean
- S4 for NVFlare: E4a ext 512 MB ok (15.5 s/round, VM 5.0 GB peak); E7 analysis (native vs hub: cnn -0.23 s, payload +0.10 s; drift = float32 aggregation amplified by SGD; accuracy follows the same oscillation, best 78.1 vs 78.4 %); E4b netem applied/cleared on request for wan1g/wan100/wan20 (fan-out 4.3/17.0/80.3 s, efficiency 0.94 at 20 Mbit/s). S4-done #959 (122 final runs), final-call #961.
- docs/report/stage2/nvflare.md final (rollout cost, limits/knobs incl. task_request_interval floor, E4a/E4b/E5/E6/E7 results, problems/fixes, recommendations); ledger rows for experiment operations and the end-state cleanup.
- End state: nvf-h2 restarted once (left the unlimited straggler scope), sysmon + run follower stopped, island files and venvs removed, extras stopped, tc default, no flhub-netem timers, both base sites HEALTHY in relay job 47c9e2b2 (flhub_core 0.2.0+fe357a1dbec1), 14 GB free.

### 2026-10-14T09:20Z — S5 — S2 all-done: DoD verified from the nvflare VM, DONE
- all-done #985 (central; REPORT_STAGE2.md §12). My report-ready #980 (last push d7fd188, end state ALL PASS).
- DoD 1: milestones on the bus: S0-ready, S1-core, S2-flower/-nvflare/-vantage6, S2-mixed, S3-frozen 96c44c9 (+ re-freeze 975a949), S4-done; 122 final runs (#959).
- DoD 3/8 (nvflare): conformance v2 vs central 13/13 (#165); endstate.sh ALL PASS: tc fq_codel default, 0 flhub-netem timers, nothing armed/throttled, no experiment processes, extras nvf-h7/nvf-h8 stopped (documented), 14 GB free, nvf-h1/nvf-h2 HEALTHY in relay job 47c9e2b2 with flhub_core 0.2.0+fe357a1dbec1.
- DoD 4: ledger nvflare rows complete (7); `git diff --stat stage1-final..HEAD -- clients/` in the ledger: clients/nvflare 0 lines.
- DoD 5: docs/report/stage2/evidence/wandb_audit.json: AgnosticFL-stage2 122 runs = runs.md rows, AgnosticFL 6 runs, ok.
- DoD 6: REPORT_STAGE2.md, 16 figures, per-agent sections (central, nvflare, vantage6, flower), ledger, README/REPORT links (1 each) on main; bus transcript follows from central.
- DoD 7: scratch venv at 0bdc1e7: pytest 103 passed, 13 skipped; conformance --adapter mock 12 passed, 1 skipped; 281 commits since stage1-final all Zare2001 (author+committer), 0 attribution lines. Scratch venv removed.
- ~/.flhub/stage2/DONE created after this push.

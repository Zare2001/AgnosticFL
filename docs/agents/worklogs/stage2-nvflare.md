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

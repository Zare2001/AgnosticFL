# Stage 2 worklog — central agent

Newest entries at the bottom. Format: `### <UTC timestamp> — S<n> — <title>` + 2–6 bullets (what, result, evidence, next).

### 2026-10-13 13:50 UTC — S0 — Kickoff checks
- Read PLAN.md, EXPERIMENTS.md, roles/CENTRAL.md, stage-1 worklog. `bash deploy/central/status.sh`: HEALTHY (6 sites online). Disk 8.2 GB free.
- Regression at cdbd693: `configs/final.yaml --rounds 3` mixed run (runs-dev/s2-s0-regression) vs `hub.sim`: max abs diff 0.0 rounds 0-3; r1-r3 byte-identical to stage-1 mixed-6sites. Bus #101.
- Stray empty files (PASS, max, finished, ...) appeared in the repo root at 14:03Z from an outside terminal paste; left untouched, never staged.

### 2026-10-13 14:15 UTC — S1 — flhub/0.2 pushed
- adapters/common: per-site task states (Task.site_state/site_envelopes), BaseAdapter.report_site/report_site_error, collect partial, cancel sites, per-site -32015, deadline fails only active sites; RelayAdapter /relay/site_result, /relay/site_error, /relay/job; optional dispatch data_variant hint; RpcServer bodies 2048 MiB with streamed blob PUT/GET; hub core DELETE /blobs + /admin/gc.
- Mock adapter: per-site reports, --site-delay/--hang/--fail/--offline. Conformance v2: subset dispatch, partial+site cancel+next dispatch, offline site, restart between rounds (11 passed vs mock). Unit tests green in hub/nvflare/v6/flower venvs.
- Commits d7418df..43c891d; bus #112 (push, pull: yes), #113 (flhub_core 0.2 API decisions for #105/#109/#110).
- Next: flhub_core 0.2 (OrganAMNIST ADR, partitioners + manifests, model zoo, train options, lazy data handle, selfcheck v2).

### 2026-10-13 14:55 UTC — S1 — Core complete (S2 S1-core)
- flhub_core 0.2 (be35072..a3d541b, ADR-009/010): OrganAMNIST (md5 = MedMNIST official), partitions iid/dir1.0/dir0.3/dir0.1/qty/fwcorr/mix/iid12 with committed manifests and heat map, lazy SiteSource data variants, model zoo (cnn-small 52,203 / cnn-medium 1,676,779 / resnet18-gn 11,173,323 params / payload-<MB> echo), FedProx mu, weight decay, metrics train_time_s/data_load_s/peak_rss_mb/data_fp, recursive code_version, selfcheck v2. code_version 0.2.0+fe357a1dbec1; selfcheck v2 digest sha256:ac074a0b...; v1 still 7c80a5a3.
- Regression: hub.sim v2 with configs/final.yaml (30 rounds, 4 workers) byte-identical to stage-1 mixed-6sites r0-r30; real 3-round run with the v2 orchestrator against the deployed 0.1 adapters (negotiated flhub/0.1) byte-identical r0-r3.
- hub/aggregators (fedavg = stage-1 code path, fedavgm, fedadam, fedyogi, fedadagrad, fedprox; float64 state, deterministic safetensors state bytes; bug found: two safetensors metadata keys serialize in varying order, fixed with one key). Orchestrator/sim v2: schema 2 + configs/stage2/topology.yaml, stateless seeded sampling, min_sites via partial collect + per-site cancel, strict pre-dispatch online wait (nvflare #124), retries, resume (bit-exact in tests), per-round global_ref/agg_state_ref, timing (t_dispatch, t_result, overhead, fanout, agg, eval), multi-class + site-shaped eval, no live W&B, sim --workers/--replay/--participants-from/--compare, centralized and local references.
- finalize_run.py (gates real/sim/reference/native, W&B AgnosticFL-stage2 only, runs.md row, retention, blob GC; tested dry-run + offline). sysmon.py (stdlib /proc, hourly JSONL, summarize; running on central since 14:47Z). netem.sh tested live (bus #125/#126) and cleared.
- Training cost per site-round on OrganAMNIST (5,761 samples, 1 thread): cnn-small 1.6 s, cnn-medium 11.4 s, resnet18-gn ~3.6 min.
- Commits 20f9421..56074ba. Next: S1-core broadcast, then S2 deploy-requests in arrival order.

### 2026-10-13 16:01 UTC — S2/S3 — rollouts, S2-mixed, tuning, freeze
- S3 tuning grid (scripts/stage2/tune.py, 21 sims, val split): lr 0.1; fedavgm 1.0 (0.914), fedadam 0.03 (0.906), fedprox 0.01 (0.903), fedadagrad 0.03 (0.885), fedyogi 0.03 (0.879); A* = fedavgm. Several optima on the grid edge (tuning.md notes). Configs: base.yaml + 98 run configs (extends), make_configs.py --check clean.
- Deploys: nvflare #141 (f76942c, kits h7/h8 bus #4/#5, hospital switch 82.6 s / 50 s offline per site) + keeper fix 05e2b2a + 3212150; flower #144 (FAB rollout 29.6 s, 0 hospital steps) + #177 + 17cdfd1 (stale-relay fix of a 97 s restart); vantage6 #174 (image v6-train:a43cef3f75c0 built+pushed in 98 s, E5 orgs/nodes, keys bus #6), adapter at 890966d. needs_weights fix in adapters/common (a43cef3, flower finding #173).
- Conformance v2 vs central: flower 13/13 (also re-run at 17cdfd1), nvflare 13/13, vantage6 13/13 (payload-64 round 117.5 s); all diffs 0.0; evidence/conformance-v2-*-central.json. Milestones S2-flower #154, S2-nvflare #165, S2-vantage6 #199.
- Real-path dev run (nvflare+flower, dir0.3, fedadam) = sim; S2-mixed (6 sites, 3 rounds, 8.6 s/round) = sim, hashes equal, milestone #200.
- S3-frozen 96c44c9 (#210, ~/flhub-runtime/frozen_sha). Frozen paths: flhub_core adapters clients hub configs/stage2 deploy/central. Waiting for the three confirmations, then S4 E4a.

### 2026-10-13 16:30 UTC — S4 — E4a running; hub core restart; disk
- Confirmations #211-#213 for 96c44c9; S4 started 16:03Z with E4a (scripts/stage2/e4a_sweep.py per scope; purge between sizes; disk/RAM rules).
- Problem found: the hub-core systemd service still ran stage-1 code (started 12:27Z): no /admin/gc or DELETE /blobs, 512 MiB body cap (would have failed E4a 512 MB inside the hub). Restarted on the frozen code before the 256/512 MB sizes; 530 MiB blob round trip verified (put 1.4 s, get 1.2 s). Lesson: every central service must be restarted after a code change in its package, not only the adapters.
- Disk: GC of old blobs (584 MB), S3 checkpoints (376 MB), payload checkpoints dropped after each run and after its INV-1 sim (hashes suffice). Free 5-6 GB -> 512 MB on 2-site scopes = testbed disk limit (needs 7 GB); asked owner #214 about the local stage-1 image copy (1.5 GB unique).
- Results so far: nvflare 1-256 MB ok (9-43 s per run), flower 1-256 MB ok (20-78 s), 12 runs finalized (INV-1 full-sim hash-equal) on W&B; flower bytes on wire ~1.002 x payload. vantage6 running: 32 MB 183 s, 64 MB ~118 s per round (DB mode).
- Mistake: a wait loop with pgrep -f matched its own command line (stage-1 lesson repeated); killed by PID.

### 2026-10-13 17:16 UTC — S4 — E4a done, E6d dry run, E1 started, re-freeze 975a949
- E4a final: nvflare 1-256 MB, flower 1-256 MB, vantage6 1-128 MB (superlinear: 64 MB 115 s/round, 128 MB 468 s/round; uwsgi single-thread CPU bound, sqlite 1.14 GB + 0.2 GB journal), mixed 1-64 MB. All stops are testbed disk limits on central (2-site 512 MB needs 7 GB; vantage6 256 MB by measured projection, disk_projection.json; mixed 128 MB needs 6 GB). No framework limit hit. Milestone S4-E4a #381; run data to agents (bus files #70-#75).
- E6d dry run (configs/dev/dry-e6d.yaml, 6 rounds, kill after r3): adapters back in 1.1/2.2/6.0 s, resume, rounds 4-6 without retry, hash-equal to sim in all rounds (incl. fedavgm state).
- Bug (frozen code, hub/ only): the E1 reference jobs failed at start: (1) config.json partition_sizes indexed the manifest with the 'pooled' pseudo-site (KeyError); (2) local-only worker function defined in hub/sim/__main__ could not be loaded by spawned workers. Fixed (975a949: hub/sim/local.py, 1-line run.py change, unit test for both modes), re-frozen at 975a949 (bus #396); earlier final runs unaffected (real runs and federated sims do not execute the changed lines). Lesson: write frozen_sha only after the push, since pull --rebase rewrites the local SHA.
- E1/E2 campaign (scripts/stage2/campaign_e1e2.sh) running since 17:12Z: s2-e1-iid-fedavg first, ~10.5 s per round incl. eval.

### 2026-10-13 18:27 UTC — S4 — E1 done, E2 started
- E1 real runs (campaign_e1e2.sh, 9.2-10.5 min each): iid 0.834, dir1.0 0.799, dir0.3 0.770, dir0.1 0.734, qty 0.838, fwcorr, mix; references centralized 0.847, local-iid mean 0.692, local-dir0.1 mean 0.263. INV-1 full sim hash-equal for every finalized real run; seed sims finalized as sim-counterpart. Label skew: high final-round variance (dir1.0 seeds 0.799/0.713/0.789), so the tables also show the mean of the last 5 rounds.
- Infra: run_final.py PAUSE hold; vantage6 purge --vacuum between E1 and E2 (714 tasks, 1.2 GB -> 156 KB, central 8.0 GB free). Owner removed the local stage-1 v6 image (#410): E4a extension (nvflare/flower 512, vantage6 256, mixed 128) scheduled at the end of S4.
- E4 bytes on wire (scripts/stage2/e4_bytes.py): nvflare 1.005-1.06 x payload, flower 1.002-1.005, vantage6 5.4 (64 MB) to 9.1 (128 MB): 4 input copies per site-round by design (vantage6 #409) + retries when the single uwsgi worker drops connections.
- Prepared: arm.py (ARM/ARMED handshake), campaign_e6.sh, inv4_check.py, report_tables.py (+ INV-1 table), plots.py E3-E7, bus_stats.py, central.md draft, REPORT_STAGE2.md setup/RQ2/threats/limitations/reproduce drafts.

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

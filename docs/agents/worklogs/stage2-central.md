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

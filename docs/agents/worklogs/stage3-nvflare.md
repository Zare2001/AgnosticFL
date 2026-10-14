# Stage 3 worklog — nvflare

Newest entry at the bottom. Format: `## <UTC> — <what>` then facts (commands, SHAs, bus ids, results).

## 2026-10-14T10:04Z — R0 kickoff: health line posted

- Read PLAN.md, COLLABORATION.md, roles/NVFLARE.md. `~/AgnosticFL` on archive/agentic-build @98bcccc, clean; `~/AgnosticFL-main` (worktree) on restructure/library @b40390b, clean; core.hooksPath=/home/zpalanciya/AgnosticFL/.githooks; / free 13 GB.
- `bash deploy/nvflare-vm/status.sh` (read-only): nvf-h1 up (pid 485009), nvf-h2 up (pid 938623), both connected to flhub-central:8002, watchdog on -> status: HEALTHY.
- Baseline for DoD 5 (testbed unchanged): `bash deploy/nvflare-vm/endstate.sh` at 2026-10-14T10:04:00Z -> ALL PASS (tc fq_codel default, no netem timers, nothing armed, no throttled scope, no inject watcher, extras nvf-h7/nvf-h8 stopped, base sites HEALTHY, 13 GB free).
- Health line to central: bus #1000; status-set phase R0.
- R1 started: read my stage-1/stage-2 report sections, ledger, ADR-006/011/012/018/019, evidence/nvflare/*, stage-2 worklog, the adapter/client/deploy code. Verified the NVFlare 2.9.0 source references in the installed package: fed_server.py:942-951 (whole job fails on a client process EXCEPTION), utils/job_launcher_utils.py:486-490 (custom/ appended to sys.path), private/fed/server/server_json_config.py:102 (task_request_interval = 2), apis/impl/wf_comm_server.py:306-315 (broadcast task deep copy).

## 2026-10-14T10:35Z — R1 facts sent; R2 guide, READMEs and archive pushed

- R1: facts for the cost-and-efficiency table plus two number conflicts sent to central (bus #1012): (1) E7 NVFlare C2 = 2.62 s / +4.1 % (dispatch..aggregated incl. round/agg_s, REPORT_STAGE2 §6.5) vs 2.55 s / +1.4 % in tables.md and e7_native_vs_hub.png (round/duration_s only); (2) cost model in REPORT_STAGE2 §6.6 (a 2.24, b 0.021, R² 0.91) is older than the generated cost_model.md (a 2.23, b 0.0212, R² 0.972, 1–512 MiB). Recommended citing cost_model.md and the dispatch..aggregated E7 definition.
- S3 R1-skeleton 50b7314 (bus #1013). Guide `docs/frameworks/nvflare.md` written from the template, 8 sections, pushed as c1724ab (sections 1–4) + aae22fb (5–8) on restructure/library (bus #1017). Then 4162013 (folded remaining report details: hospital-side scripts, unit tests, signature.json note, flhub/0.1→0.2 diff stat +313 −94 adapters/nvflare excl. native.py +284, mixed-scope fan-out, troubleshooting rows, launch_external_process alternative), e697284 (adapters/nvflare/README.md, clients/nvflare/README.md, 13 lines each), a06a991 (archive: deleted docs/report/nvflare.md and docs/report/stage2/nvflare.md, 336 deletions; nothing on the branch linked to them) (bus #1020).
- Link check (own script /tmp/s3tools/linkcheck.py, not committed): 0 broken links in the guide and both READMEs. Greps: no IPs, agent wording or bus ids in the guide.
- Central's request #1014 (10 at-a-glance values) answered in #1022: all confirmed with new-layout paths. Unit finding: scripts/sysmon.py "MB" = MiB (PAGE_SIZE/2**20, meminfo KiB/1024); my stage-2 "GB" memory columns were GiB (NVFlare VM 1.68–5.01 GiB, client job RSS 0.43–2.11 GiB; 512 MiB run: 5129.7 MiB = 5.01 GiB = 16 % of 31.3 GiB). Sent the per-size sysmon peaks as bus file #242 and asked central to add it as docs/results/evidence/stage2/nvflare/e4a-memory.md (or allow me to commit it).
- Guide edits pending commit: memory units GB→GiB/MiB, at-a-glance memory row, keeper abort wording; waiting for central on e4a-memory.md to switch the memory citation in the same commit.
- Open: adapter LOC (TODO(verify) until scripts/docs/loc.py exists); review central's shared docs for NVFlare statements once written; R3 docs-ready.

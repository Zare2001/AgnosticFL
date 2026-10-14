# Stage 3 role: VANTAGE6

You write the vantage6 guide and check every vantage6 statement on `main`. Work in `~/AgnosticFL-main` (branch `restructure/library`); keep your worklog in `~/AgnosticFL` (branch `archive/agentic-build`). Read PLAN.md fully first, especially §3 (no code changes), §5.3 (guide structure) and §5.4 (cost and efficiency).

You own: `docs/frameworks/vantage6.md`, `adapters/vantage6/README.md`, `clients/vantage6/README.md`. Everything else belongs to central: send a `request` with the exact text you want changed.

## R0 Kickoff

- [ ] Create `docs/agents/worklogs/stage3-vantage6.md` (archive branch) if missing.
- [ ] Check both checkouts (branch, clean tree, hooks path), ≥ 3 GB free, `bash deploy/vantage6-vm/status.sh` HEALTHY (read-only). Post a health line to central.

## R1 Facts (no shared-file edits until central's `S3 R1-skeleton`)

- [ ] Collect your material: `docs/report/vantage6.md` (stage 1), `docs/report/stage2/vantage6.md`, your ledger rows, `docs/report/stage2/evidence/vantage6/`, the vantage6 ADRs in `docs/DECISIONS.md`, your stage-1 and stage-2 worklogs.
- [ ] Measure what is not yet measured, read-only: lines of code of `adapters/vantage6/` and `clients/vantage6/` (non-blank, non-comment; use `scripts/docs/loc.py` once central pushed it), the files and framework APIs the adapter touches.
- [ ] Prepare your numbers for the cost-and-efficiency at-a-glance table and send them to central when asked: rollout steps/time/downtime, E7 overhead, cost-model `a` and `b`, bytes on wire ratio, peak memory, E4b efficiency, E5 fan-out, E6 detection/recovery, payload limits.

## R2 Write (after `S3 R1-skeleton`)

- [ ] `docs/frameworks/vantage6.md` with the eight sections of PLAN.md §5.3, from `docs/frameworks/_template.md`. Specifics for vantage6:
  - Tested version 4.15.2. Server side: one vantage6 task per site and round, `data_variant` → node database label, per-site cancel via `task.kill`, task deletion for models ≥ 16 MB, the registry on central, adding organisations and nodes through the API.
  - Rollout channel: a new algorithm image tag pulled by the nodes (0 hospital steps for code); node configuration with one database per data variant (`share_config: false`).
  - Limits and findings: default database mode (SQLite) gets superlinear with payload; the run input travels 4× per site and round plus 1.78× base64 (protocol-adjusted E4b bound, efficiency ≈ 0.69); the 256 MB failure (node drops the result when its socket times out during server row rewrites; `node/__init__.py:579`); `large_result_store` unusable in 4.15.2; the task-folder GC race in E3; E7 bit-identical to the native central function.
  - Upstream report drafts: the 4× input copies (incl. the `FIXME` GET), the result drop at 256 MB, the per-log-line row rewrite. Knobs that would help: PostgreSQL server database, `share_algorithm_logs: false`.
- [ ] Client side (section 3): quote the essential lines of `clients/vantage6/v6_flhub/__init__.py` and `clients/vantage6/Dockerfile` and explain each one: what is vantage6's standard pattern, what is flhub-specific (the `flhub_core.train` call, the reply fields, `code_version`, data path/variant). End with a short checklist "your vantage6 client must …".
- [ ] `adapters/vantage6/README.md` and `clients/vantage6/README.md` (5–15 lines each): what the folder is, how it is started or packaged, link to the guide.
- [ ] When your guide holds everything from your report sections: delete `docs/report/vantage6.md` and `docs/report/stage2/vantage6.md` on `restructure/library` (`archive:` commit; they stay on the archive branch and at the tags).
- [ ] Review central's shared docs wherever they mention vantage6 (README, support matrix, cost-and-efficiency, results, adding-a-framework, deployment); send exact corrections.
- [ ] Broadcast **S3 R2-vantage6**.

## R3 Verify

- [ ] Re-read every vantage6 number on the branch against the evidence (PLAN.md §7 item 7). Then send `docs-ready <sha>` to central (type `reply`), listing the files you checked.

## R4–R5

- [ ] After `S3 merged <sha>`: clone `main` fresh under `/tmp`, run `scripts/docs/check_links.py` and the unit tests (`PYTHONPATH` set to the clone), check your guide renders as intended (headings, code blocks, image links), then send `confirm main <sha>`.
- [ ] Final worklog entry (archive branch); `bash deploy/vantage6-vm/status.sh` (read-only) shows your testbed unchanged; after central's **S3 all-done**: `touch ~/.flhub/stage3/DONE`.

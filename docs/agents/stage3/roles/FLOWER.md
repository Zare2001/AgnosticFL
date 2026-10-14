# Stage 3 role: FLOWER

You write the Flower guide and check every Flower statement on `main`. Work in `~/AgnosticFL-main` (branch `restructure/library`); keep your worklog in `~/AgnosticFL` (branch `archive/agentic-build`). Read PLAN.md fully first, especially §3 (no code changes), §5.3 (guide structure) and §5.4 (cost and efficiency).

You own: `docs/frameworks/flower.md`, `adapters/flower/README.md`, `clients/flower/README.md`. Everything else belongs to central: send a `request` with the exact text you want changed.

## R0 Kickoff

- [ ] Create `docs/agents/worklogs/stage3-flower.md` (archive branch) if missing.
- [ ] Check both checkouts (branch, clean tree, hooks path), ≥ 3 GB free, `bash deploy/flower-vm/status.sh` HEALTHY (read-only). Post a health line to central.

## R1 Facts (no shared-file edits until central's `S3 R1-skeleton`)

- [ ] Collect your material: `docs/report/flower.md` (stage 1), `docs/report/stage2/flower.md`, your ledger rows, `docs/report/evidence/*flower*` and the Flower parts of `docs/report/stage2/evidence/`, the Flower ADRs in `docs/DECISIONS.md`, your stage-1 and stage-2 worklogs.
- [ ] Measure what is not yet measured, read-only: lines of code of `adapters/flower/` and `clients/flower/` (non-blank, non-comment; use `scripts/docs/loc.py` once central pushed it), the files and framework APIs the adapter touches.
- [ ] Prepare your numbers for the cost-and-efficiency at-a-glance table and send them to central when asked: rollout steps/time/downtime, E7 overhead, cost-model `a` and `b`, bytes on wire ratio, peak memory, E4b efficiency, E5 fan-out, E6 detection/recovery, payload limits.

## R2 Write (after `S3 R1-skeleton`)

- [ ] `docs/frameworks/flower.md` with the eight sections of PLAN.md §5.3, from `docs/frameworks/_template.md`. Specifics for Flower:
  - Tested version 1.39.0. Server side: the relay ServerApp run on the SuperLink, the adapter's relay launcher and stale-FAB handling, ports 9092/9093, insecure mode in the testbed (TLS as future work), SuperLink database growth and purge.
  - Rollout channel: the FAB carries `flhub_core`; 0 hospital steps and 0 restarts; FAB file rules and the 10 MiB limit.
  - Limits and findings: gRPC 2 GiB per message; fixed ~5 s per round (fresh ClientApp per message, 3 s polling); 512 MB passed; wire ratio 1.002–1.005; E7 bit-identical to native FedAvg (and the float32 drift with unequal sizes found on the island).
  - Upstream report drafts: only if you found a Flower bug; otherwise list improvement ideas.
- [ ] Client side (section 3): quote the essential lines of `clients/flower/flhub_flower/client_app.py` and `clients/flower/flhub_flower/records.py` (and explain that `server_app.py` is the central-side relay, not hospital code) and explain each one: what is Flower's standard pattern, what is flhub-specific (the `flhub_core.train` call, the reply fields, `code_version`, data path/variant). End with a short checklist "your Flower client must …".
- [ ] `adapters/flower/README.md` and `clients/flower/README.md` (5–15 lines each): what the folder is, how it is started or packaged, link to the guide.
- [ ] When your guide holds everything from your report sections: delete `docs/report/flower.md` and `docs/report/stage2/flower.md` on `restructure/library` (`archive:` commit; they stay on the archive branch and at the tags).
- [ ] **Quick-start verification** (PLAN.md §7 item 3): when central announces `getting-started.md` ready, make a fresh venv and a fresh clone of `restructure/library` under `/tmp` and run every command in `docs/getting-started.md` exactly as written (use free ports only if the doc says so). Report the full result to central; propose doc fixes, never code fixes. Remove the temporary venv, clone and processes afterwards.
- [ ] Review central's shared docs wherever they mention Flower (README, support matrix, cost-and-efficiency, results, adding-a-framework, deployment); send exact corrections.
- [ ] Broadcast **S3 R2-flower**.

## R3 Verify

- [ ] Re-read every Flower number on the branch against the evidence (PLAN.md §7 item 7). Then send `docs-ready <sha>` to central (type `reply`), listing the files you checked.

## R4–R5

- [ ] After `S3 merged <sha>`: clone `main` fresh under `/tmp`, run `scripts/docs/check_links.py` and the unit tests (`PYTHONPATH` set to the clone), check your guide renders as intended (headings, code blocks, image links), then send `confirm main <sha>`.
- [ ] Final worklog entry (archive branch); `bash deploy/flower-vm/status.sh` (read-only) shows your testbed unchanged; after central's **S3 all-done**: `touch ~/.flhub/stage3/DONE`.

# Stage 3 role: NVFLARE

You write the NVFlare guide and check every NVFlare statement on `main`. Work in `~/AgnosticFL-main` (branch `restructure/library`); keep your worklog in `~/AgnosticFL` (branch `archive/agentic-build`). Read PLAN.md fully first, especially §3 (no code changes), §5.3 (guide structure) and §5.4 (cost and efficiency).

You own: `docs/frameworks/nvflare.md`, `adapters/nvflare/README.md`, `clients/nvflare/README.md`. Everything else belongs to central: send a `request` with the exact text you want changed.

## R0 Kickoff

- [ ] Create `docs/agents/worklogs/stage3-nvflare.md` (archive branch) if missing.
- [ ] Check both checkouts (branch, clean tree, hooks path), ≥ 3 GB free, `bash deploy/nvflare-vm/status.sh` HEALTHY (read-only). Post a health line to central.

## R1 Facts (no shared-file edits until central's `S3 R1-skeleton`)

- [ ] Collect your material: `docs/report/nvflare.md` (stage 1), `docs/report/stage2/nvflare.md`, your ledger rows, `docs/report/stage2/evidence/nvflare/`, the NVFlare ADRs in `docs/DECISIONS.md`, your stage-1 and stage-2 worklogs.
- [ ] Measure what is not yet measured, read-only: lines of code of `adapters/nvflare/` and `clients/nvflare/` (non-blank, non-comment; use `scripts/docs/loc.py` once central pushed it), the files and framework APIs the adapter touches.
- [ ] Prepare your numbers for the cost-and-efficiency at-a-glance table and send them to central when asked: rollout steps/time/downtime, E7 overhead, cost-model `a` and `b`, bytes on wire ratio, peak memory, E4b efficiency, E5 fan-out, E6 detection/recovery, payload limits.

## R2 Write (after `S3 R1-skeleton`)

- [ ] `docs/frameworks/nvflare.md` with the eight sections of PLAN.md §5.3, from `docs/frameworks/_template.md`. Specifics for NVFlare:
  - Tested version 2.9.0. Server side: the relay job (one NVFlare task per site via `Controller.send`, `adapters/nvflare/relay_controller.py`), the site watcher and keeper (`service.py`, `flare_admin.py`), ports 8002/8003, provisioning and startup kits, adding sites without a server restart.
  - Rollout channel: the job's `custom/` code; the one-time venv cleanup and client restart at the hospitals, then 0 steps for later versions.
  - Limits and findings: a training-process crash fails the whole relay job (both sites lose the round; `fed_server.py`); a restarted client is readmitted only by a relay-job restart while the hub is idle; a cancelled task keeps training; the ~2 s `task_request_interval` floor; 512 MB passed on central, 1024 MB on the island; E7 float32 aggregation drift and why accuracy trajectories differ; wire ratio 1.005–1.06.
  - Upstream report drafts: the whole-job failure on a training-process crash; no per-task abort.
- [ ] Client side (section 3): quote the essential lines of `clients/nvflare/client.py` and explain each one: what is NVFlare's standard pattern, what is flhub-specific (the `flhub_core.train` call, the reply fields, `code_version`, data path/variant). End with a short checklist "your NVFlare client must …".
- [ ] `adapters/nvflare/README.md` and `clients/nvflare/README.md` (5–15 lines each): what the folder is, how it is started or packaged, link to the guide.
- [ ] When your guide holds everything from your report sections: delete `docs/report/nvflare.md` and `docs/report/stage2/nvflare.md` on `restructure/library` (`archive:` commit; they stay on the archive branch and at the tags).
- [ ] Review central's shared docs wherever they mention NVFlare (README, support matrix, cost-and-efficiency, results, adding-a-framework, deployment); send exact corrections.
- [ ] Broadcast **S3 R2-nvflare**.

## R3 Verify

- [ ] Re-read every NVFlare number on the branch against the evidence (PLAN.md §7 item 7). Then send `docs-ready <sha>` to central (type `reply`), listing the files you checked.

## R4–R5

- [ ] After `S3 merged <sha>`: clone `main` fresh under `/tmp`, run `scripts/docs/check_links.py` and the unit tests (`PYTHONPATH` set to the clone), check your guide renders as intended (headings, code blocks, image links), then send `confirm main <sha>`.
- [ ] Final worklog entry (archive branch); `bash deploy/nvflare-vm/status.sh` (read-only) shows your testbed unchanged; after central's **S3 all-done**: `touch ~/.flhub/stage3/DONE`.

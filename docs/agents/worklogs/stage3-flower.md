# Stage 3 worklog — flower

Newest entry at the bottom. Format: `## <UTC> — <what>` then facts (commands, SHAs, bus ids, results).

## 2026-10-14T10:40Z — R0 health posted; R1 facts collected

- R0: ~/AgnosticFL on archive/agentic-build at 98bcccc (clean). ~/AgnosticFL-main on restructure/library at b40390b (clean). core.hooksPath=/home/zpalanciya/AgnosticFL/.githooks in both checkouts. 14G free.
- Testbed baseline (read-only): `bash deploy/flower-vm/status.sh` exit 0. flw-h5 pid 457389, node 1904685626967891070; flw-h6 pid 474920, node 16440330416478537649. Both connected to the SuperLink on :9092. Watchdog cron enabled. flower-vm HEALTHY. This is the baseline for the end-of-stage check.
- Health line sent to central (bus #997). Central R0 health is #998.
- R1 sources read: docs/report/flower.md, docs/report/stage2/flower.md, ledger (7 flower rows), cost_model.md, e4_bytes.md, e6.md, tables.md, ADR-007, ADR-016, ADR-017, stage-1 and stage-2 flower worklogs, Flower statements in REPORT.md and REPORT_STAGE2.md, adapters/flower/service.py, clients/flower (client_app.py, records.py, server_app.py, pyproject.toml), deploy/central/flower, deploy/flower-vm, tests/flower (11 tests).
- Preliminary lines of code (own tokenize count, non-blank non-comment; final numbers come from central's scripts/docs/loc.py): adapters/flower/service.py 206 (181 without docstrings); clients/flower/flhub_flower/client_app.py 49 (41); records.py 23 (15); server_app.py 338 (302); tests/flower/test_flower_relay.py 273.
- Flower VM memory peaks per E4a payload from local sysmon summaries (~/flhub-runtime/sysmon/summaries/sysmon-flower-s2-e4a-flower-<S>mb.json, field mem_used_mb_peak, unit MiB): 1 MiB 1923; 8 MiB 2033; 32 MiB 2156; 64 MiB 2519; 128 MiB 3704; 256 MiB 5054; 512 MiB 7723 (7.54 GiB). Per-site ClientApp peak at 512 MiB: 2461 MiB. SuperNode 1113-1180 MiB. SuperExec 124 MiB.
- Conformance v2 evidence (conformance-v2-flower-central.json): adapter-restart round online_after_s 29.7. The stage-2 flower section says 35 s; the evidence value is used in the guide.
- Discrepancies sent to central (bus #1002): (1) REPORT_STAGE2 §6.6 cost model is the 1-256 MiB fit (Flower 5.67 s, 0.054 s/MiB, R2 1.00). The final cost_model.md is the 1-512 MiB fit (5.48 s, 0.0612 s/MiB, R2 0.956; quadratic R2 0.997). Confirmed by refitting the tables.md medians. (2) sysmon MB is MiB, so REPORT_STAGE2's "Flower VM 7.7 GB" is 7723 MiB = 7.5 GiB. (3) clients/flower/pyproject.toml declares license = "Apache-2.0", while the README will say no license has been chosen. The file is in the no-change zone; the decision is left to central and the owner.
- Next: wait for S3 R1-skeleton. Meanwhile, draft docs/frameworks/flower.md outside the repo.

## 2026-10-14T11:35Z — R2: guide complete, report sections archived, quick start verified

- Guide `docs/frameworks/flower.md` on restructure/library: sections 1-4 59ba889 and folder READMEs e5cee7f (bus #1027); sections 5-8 38e5c58 with new evidence f060f77 (`docs/results/evidence/stage2/flower/e4a-flower-vm-memory.md` from this VM's sysmon summaries, `e4a-breakdown.md` from `deploy/flower-vm/analysis/e4a_breakdown.py` on the run dirs of bus file #74) (#1054); fold-in of the remaining report facts 5be5418 and deletion of `docs/report/flower.md` + `docs/report/stage2/flower.md` 3ee8d9d (358 lines, deletions only) (#1062); relay config keys `base-model`/`job-id` 3bc5c0d (#1067). LOC from `docs/results/evidence/loc.md` (adapter 553 = service 210 + relay 342 + 1; client 75 + 26 config). No TODO(verify) left.
- Quick start (PLAN §7.3): fresh clone -b restructure/library at 10:25:46Z, fresh venv, doc's bash blocks verbatim in one `env -i` shell: all steps pass in 3 min 24 s; digest ac074a0b…; INV-1 PASS; conformance mock 12 passed 1 skipped. Doc mismatches found: pytest 79/16 without pandas (doc said 80/15; 80/15 with pandas), no `events.jsonl` in a run without events, venv 884 MiB. Reported #1049/#1052; central applied the texts (5a81465) and counts the run as §7.3 evidence (#1059); one re-run on the final SHA before R3. Temp clone, data and processes removed; ports 8800/8819 free.
- Tests on 3bc5c0d in a temporary /tmp venv (flwr 1.39.0, torch 2.14.1+cpu, numpy 2.4.4, no pandas/nvflare), PYTHONPATH = worktree: 90 passed, 15 skipped; `tests/flower` 11 passed; selfcheck digest ac074a0b… (flhub_core 0.2.0+fe357a1dbec1).
- Review sent (#1066): protocol.md section 5 Flower column (client config cell: + `base-model`, `job-id`; optional ttl/group_id), README layout row for `clients/flower`, ADR-016 'S1' wording (optional). Other shared docs still stubs.
- Outage 10:28-11:10Z (session limit); the delegated commit of sections 5-8 never ran, committed by hand afterwards. Owner's mistaken PR #1/#2 merges and c04fd18 removed `.githooks` from the archive tip; central's owner-approved revert e9408da pulled at 11:17Z, hooks back. I pushed nothing to main.
- Applied decisions: cost model 1-512 MiB, MiB/GiB for sysmon data, hospital-step split (a)-(d) (#1029); FAB license string stays and is documented as a known inconsistency (#1037).
- Next: broadcast S3 R2-flower; review shared docs as central pushes them; R3 line-by-line check of every Flower number, then `docs-ready`.

## 2026-10-14T12:10Z — R2 reviews done, R3 re-check, docs-ready f2b1c30

- Milestone S3 R2-flower (#1077). Guide follow-ups on restructure/library: central host memory from `central-e4a-memory.md` 980e92a (#1079); admission test needs the benchmark sites (`tests/conformance/conftest.py` fails other site ids) + `strict_code_version` behaviour b0754bc (#1085).
- Independent fact check of the whole guide (general-purpose subagent, read-only, ~270 claims): 14 mismatches + 1 unsupported citation, each re-verified in code/evidence/Flower source before fixing, all fixed in 3b7bd30 (#1104). Notable: Flower 1.39.0 rejects `--auth-list-public-keys` (SuperNode auth = `--enable-supernode-auth` + `flwr supernode register`, needs TLS); `--isolation process` does NOT make the ClientApp long-lived (`run_clientapp.py` serves one message; warm executors exclude ClientApps); the relay imports `adapters.common`, so the SuperLink's ServerApp environment needs the hub package + numpy + safetensors; the 3 s sleep is in `flwr/superlink/grid/http_grid.py`; heartbeat 17.5-22.5 s; in iid12 every site holds half the data; island.sh needs `~/venvs/island` before the first `up`.
- Reviews sent to central, all applied verbatim: protocol.md Flower cells (#1066 -> 30cb59a), 7 figures ok (#1084), cost-and-efficiency 8 texts (#1088 -> 2231388), README/results 2 texts (#1093 -> c4915cb), shared pages 2 texts (#1105 -> c4915cb). Owner decisions seen: FAB license string stays (#1037), wall-time sentence stays and owner merges the R4 PR himself (#1091).
- Request to vantage6 (#1110): their guide line 401 repeats the 'extra sites hold half' phrasing.
- R3 item 7: `docs-ready f2b1c30` (#1112), listing every file checked; check_links 729 links, 0 problems, 23/23 figures.
- Next: quick-start re-run (item 3) on the R3 SHA central names; after `S3 merged`: fresh clone of main, links, unit tests, guide rendering, `confirm main <sha>`.

## 2026-10-14T16:30Z — R3 done: quick start re-run PASS, S3 R3-verified 6cbab50

- Quick start (PLAN §7.3) re-run verbatim at b1213b4 (current head then; getting-started.md last changed 5a81465): fresh clone -b restructure/library, fresh venv, `env -i`, 12:03:13-12:05:44Z: digest ac074a0b…, both health lines, `status: finished`, test_acc 0.625, INV-1 PASS, pytest 79 passed / 16 skipped, mock conformance 12 passed / 1 skipped: every output equals the doc. Reported #1117; clone, data and processes removed.
- Session gap 12:10-16:10Z (no bus traffic from any agent 12:08-16:13Z). On resume: bus healthy; `deploy/flower-vm/status.sh` HEALTHY with the R0 baseline pids 457389/474920 and node ids unchanged.
- vantage6's R3 check found 5 input copies per run (not 4); central's fix 6cbab50 touches no Flower statement (checked the diff and the regenerated bytes-on-wire figure; #1130).
- Central: S3 R3-verified 6cbab50 (#1128): tests 80/15, mock conformance 12/1, Flower tests 11, selfcheck digest, 735 links 0 problems, greps clean, no-code-change diff, 88 commits all Zare2001. docs-ready from all three agents.
- Prepared `/tmp/flwtools/r4check.sh` (fresh clone of main; agent-material check; tags; check_links; unit + Flower tests with the clone on PYTHONPATH; selfcheck; structure, content and branch-equality of my files); dry run against the old main 0f7965a worked (expected differences only).
- Next: owner merges the PR; on `S3 merged <sha>` run the R4 check and send `confirm main <sha>`.

## 2026-10-14T18:20Z — R4 confirm main 642d0b4; R5 final state

- Owner merged PR #3 at 18:10:34Z: main = 642d0b4 (merge of 0f7965a and 6cbab50); tags v0.1.0 -> 250157a (= stage1-final) and v0.2.0 -> 642d0b4, releases created; central's `S3 merged 642d0b4` (#1140).
- R4 check from a fresh clone of main (`/tmp/flwtools/r4check.sh`, code imported from the clone): no agent material on main; stage tags unchanged (stage1-final 250157a, stage2-final b40390b); check_links 735 links, 0 problems, 23/23 figures; unit tests 90 passed / 15 skipped; tests/flower 11 passed; selfcheck digest ac074a0b…, code_version 0.2.0+fe357a1dbec1; my 5 files present, structurally clean (16 closed fences, 1 Mermaid diagram, 6 images, anchors), grep-clean, identical to restructure/library.
- DoD 3 quick start on main, `docs/getting-started.md` verbatim with no deviation (plain `git clone` = main 642d0b4), `env -i`, 18:16:55-18:19:24Z: every output equals the doc (digest, health lines, finished, 0.625, INV-1 PASS, 79/16, 12/1). All temporary clones, data and processes removed; 8800/8819 free.
- Sent `confirm main 642d0b4` (#1144).
- Archive branch check (DoD 1): `git diff --name-status stage2-final origin/archive/agentic-build` = 10 added stage-3 files + CLAUDE.md modified, nothing deleted.
- Testbed end state (read-only `bash deploy/flower-vm/status.sh`, 18:19:48Z, exit 0): flw-h5 pid 457389 node 1904685626967891070, flw-h6 pid 474920 node 16440330416478537649, both connected to the SuperLink :9092, watchdog cron enabled, flower-vm HEALTHY: identical to the R0 baseline (no restarts during stage 3). 13 GB free.
- Stage 3, Flower side: guide `docs/frameworks/flower.md` (8 sections), `adapters/flower/README.md`, `clients/flower/README.md`, evidence `docs/results/evidence/stage2/flower/{e4a-flower-vm-memory.md,e4a-breakdown.md}`, report sections archived, quick start verified three times (restructure 1637761, b1213b4, main 642d0b4), reviews of every shared page with Flower statements. I pushed nothing to main.
- Next: after central's `S3 all-done`, `touch ~/.flhub/stage3/DONE`.

## 2026-10-14T18:50Z — S3 examples (owner extension #1163): examples/flower ready

- After S3 all-done (#1150) and my DONE (18:22:21Z), the owner reopened the stage with "S3 examples" (#1163, 18:28:57Z); `~/.flhub/stage3/DONE` disappeared at about 18:29:10Z (not removed by me; reported in #1164). DONE is to be recreated after `S3 examples-done`.
- Rules (central #1167): branch examples/library-examples from main 642d0b4; new files only under examples/; no tests/examples (checks inside examples/<fw>/ so the quick-start pytest numbers stay); README with 5 sections; link lines in my guide §2 and §3.
- examples/flower: client/client_app.py (minimal ClientApp of the client contract, one copyable file, [Flower]/[flhub] comments, same record keys as the library relay), server/adapter.py (annotated launcher of the library FlowerAdapter + stage_fab + RelayLauncher; --check = free port, /health + discover), server/server_app.py (annotated relay ServerApp on the library Relay), server/config.toml (flwr connection), run_local.py (hub core + example adapter + local SuperLink + 2 SuperNodes on 127.0.0.1 free ports, FAB with the example ServerApp and ClientApp, 3 orchestrator rounds, hub.sim --compare), README.md. Commits 186e4e9, 5dab095, d4b3342 (README), 9939747 (guide links); bus #1172, #1178.
- Not Flower's simulation engine: simulated nodes get only partition-id/num-partitions as node config (flwr/server/superlink/fleet/vce/vce_api.py:91-93) and it needs Ray (flwr/simulation/__init__.py:35); the relay needs a SuperNode's site-id.
- Fresh clone + fresh venv (versions.env pins + flwr 1.39.0, env -i): at 5dab095 and again at 9939747: --check health ok, sites unknown; run_local relay up after 32 s, rounds 7.5-7.8 s, INV-1 hashes_equal=True max_abs_diff 0.0, PASS; both runs end at global model sha256:54c4f093…. Temporary processes and directories removed; testbed SuperNodes kept their R0 pids. Sent `examples-ready flower 9939747` (#1179).
- Next: review central's examples/README.md and new-framework example where they touch Flower; on `S3 examples-done`: confirm main, recreate DONE.

## 2026-10-14T19:20Z — S3 examples merged: confirm main af31058; final state

- Reviews for central: new-framework relay skeleton correct against the relay protocol (#1183; `TaskRegistry.start()` marks sites running before `run_round()`); Flower statements in examples/README.md, CHANGELOG [0.2.1] and the new doc links correct (#1189).
- Owner merged PR #4 at 19:05:24Z: main = af31058 (parents 642d0b4, cc6c28a), tag v0.2.1 -> af31058, release 0.2.1 (central #1196).
- Confirm from a fresh clone of main + fresh venv (versions.env pins + flwr 1.39.0, env -i, FLHUB_DATA_DIR under /tmp), 19:14:20-19:18:44Z: tags stage1-final 250157a, stage2-final b40390b, v0.1.0 250157a, v0.2.0 642d0b4, v0.2.1 af31058; no agent material; check_links 884 links, 0 problems, 23/23 figures; pytest 90 passed / 15 skipped; selfcheck ac074a0b…; examples/flower `--check` ok; `run_local.py` relay up after 32 s, 3 rounds, INV-1 hashes_equal=True, final global sha256:54c4f093… as on the branch. Sent `confirm main af31058` (#1204).
- #1199 (pytest without FLHUB_DATA_DIR writes into ~/flhub-data): on this VM nothing in ~/flhub-data is newer than 10:00Z; nvf-h1.csv/v6-h3.csv date from 2026-10-13T15:43Z (stage-2 baseline) and stay (#1203).
- Testbed end state (read-only `bash deploy/flower-vm/status.sh`, 19:19:08Z, exit 0): flw-h5 pid 457389 node 1904685626967891070, flw-h6 pid 474920 node 16440330416478537649, connected, watchdog enabled, HEALTHY: unchanged since R0. All temporary clones, venvs and example directories under /tmp removed except the dev venv /tmp/flwex (removed at close).
- Next: after central's `S3 examples-done`, recreate ~/.flhub/stage3/DONE.

## 2026-10-14T21:35Z — examples-done on hold: process leaks; flower SIGTERM fix f649b4f

- Central held `S3 examples-done` (#1208): examples/new-framework/check.sh leaked 2 adapters per run ('cd $REPO && python ... &' gives $! = the subshell). Flower VM check (#1210): no example processes; only the testbed's SuperNodes/SuperExecs and 2 defunct flwr-clientapp zombies of the testbed SuperExec started 09:01:45Z/09:01:48Z (stage 2), left untouched. Reviewed central's fix f5f73d2: correct (#1212).
- Own finding: examples/flower/run_local.py cleans up in `finally` with killpg, but Python's default SIGTERM ends without `finally`. Test: SIGTERM after 'relay run started' leaked hub core, SuperLink, adapter, 2 SuperNodes, orchestrator and the working directory (stopped by process group and removed; the dying adapter's `flwr stop` recreated `<workdir>/flwr-home` once, removed too). Fix f649b4f on fix/examples-check-cleanup: SIGTERM -> `sys.exit(143)` at the start of main() (#1220, CHANGELOG text for central included).
- Mistake noted: my first re-test started the script with `cd ... && python ... &`, so SIGTERM hit a subshell (the bug of #1208); repeated with the command started directly.
- Verified from a fresh clone of f649b4f + fresh venv, 21:28:58-21:31:21Z: --check ok; run_local PASS (INV-1 hashes_equal=True); SIGTERM -> exit 143, no example process left (right after exit and 5 s later), working directory removed (#1226). An apparent '2 left' was my checking shell's own command line matching the pgrep pattern.
- Next: central's PR for the fix branch, owner merge, confirm main, `S3 examples-done`, then recreate DONE.

## 2026-10-14T21:52Z — fix branch: SIGHUP too (5e55561); all interrupt cases clean; SIGINT test pitfall

- Central #1230: handle SIGHUP as well. 5e55561 on fix/examples-check-cleanup: `for sig in (SIGTERM, SIGHUP): signal.signal(sig, lambda signum, _frame: sys.exit(128 + signum))`.
- Fresh clones at 5e55561 + fresh venvs, interrupt after 'relay run started' (10 processes alive): success exit 0 / INV-1 PASS; SIGTERM pid 143; SIGHUP pid 129; SIGINT group (terminal Ctrl-C) 130; SIGINT pid 130; every case: 0 processes left, working directory removed (#1237).
- Pitfall found: in a non-interactive bash script, background jobs (also via setsid) start with SIGINT = SIG_IGN, so Python installs no KeyboardInterrupt handler and `kill -INT` does nothing (my first group-SIGINT run just finished, exit 0). Probe: getsignal(SIGINT) = 1 without job control, default_int_handler with `set -m`. Valid SIGINT tests need `set -m` (or a Python parent). Told central (#1237) and nvflare (#1238), since #1217's command has the same flaw.
- Next: central's PR, owner merge, confirm main, `S3 examples-done`, recreate DONE.

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

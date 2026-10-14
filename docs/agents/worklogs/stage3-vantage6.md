# Stage 3 worklog — vantage6

Newest entry at the bottom. Format: `## <UTC> — <what>` then facts (commands, SHAs, bus ids, results).

## 2026-10-14T10:20Z — R0 kickoff done; R1 fact collection

- R0 checks (10:03Z): ~/AgnosticFL on archive/agentic-build 98bcccc, clean; ~/AgnosticFL-main on restructure/library b40390b, clean; core.hooksPath=/home/zpalanciya/AgnosticFL/.githooks in both; 6.6 GB free; git identity Zare2001.
- Testbed baseline (read-only): `bash deploy/vantage6-vm/status.sh` exit 0: v6-h3 OK and v6-h4 OK, node image ghcr.io/vantage6/infrastructure/node:4.15.2, connected to the central server :7601 (mode central). Containers vantage6-v6-h3-user id 7a8af5ffe077 and vantage6-v6-h4-user id 93dda6bf2aad, both started 2026-10-13T15:36Z, restart count 0. These values are the stage-3 start state to compare against in R5.
- Health line sent to central: bus #999; status board phase R0.
- R1 sources read: docs/report/vantage6.md, docs/report/stage2/vantage6.md, docs/report/stage2/{ledger,cost_model,e4_bytes,e6,tables}.md, REPORT.md, REPORT_STAGE2.md, ADR-004/005/013/014/015/020/021/022, stage-1 and stage-2 vantage6 worklogs, adapters/vantage6, clients/vantage6, deploy/central/vantage6, deploy/vantage6-vm.
- Corrections found for the guide (not yet in any document on restructure/library):
  - (1) E6a-minsites: docs/report/stage2/vantage6.md §5 says the round "went on with the other five sites"; e6.md and REPORT_STAGE2.md §7 show round 10 failed once (nvf-h1 was still out, so only 4 sites < min_sites 5) and was retried with 6 sites (detection 2.1 s, recovery 32.3 s).
  - (2) E4b protocol-adjusted efficiency: the stage-2 section says ~0.69 (it rounded 17.8 x 45 MiB to 800 MB); cost_model.md gives V·P/rate = 336.0 s at wan20 against 463.8 s measured = 0.72 (REPORT_STAGE2 says "about 0.7").
  - (3) The adapter calls `client.setup_encryption(None)`, so it supports only unencrypted collaborations as built; an encrypted collaboration needs a code change (to be listed as a limitation).
- vantage6 4.15.2 source references re-verified read-only from the PyPI wheels vantage6-node/-server 4.15.2 (downloaded to /tmp, not installed) and the client in ~/venvs/v6:
  - node/__init__.py:575-579 (status emit after get_result; result lost when the emit fails; caught at 633-634 as "Speaking thread had an exception").
  - node/__init__.py:595-598 (FIXME GET run/<id>).
  - server/resource/run.py:716 (PATCH returns the run incl. input).
  - server/resource/task.py:1289-1301 (_delete_subtasks keeps runs).
  - server/model/run.py:58-68 (input is the first Run column).
  - server/websockets.py:442-444 (on_algorithm_log saves the run per line).
  - node docker/task_manager.py:450-452 (task folders never deleted).
  - share_algorithm_logs (docker_manager.py:190 default true; task_manager.py:955-957 gates the live algorithm_log emits).
- Adapter measurements (read-only): adapters/vantage6/service.py uses 14 distinct vantage6 client calls (UserClient, authenticate, setup_encryption, util.get_server_version, collaboration.list, organization.list, GET node, task.create, GET task/<id>/status, result.from_task, GET run, task.kill, task.delete, check_if_blob_store_enabled); tests/unit/test_vantage6_adapter.py has 10 tests (9 functions, one parametrized twice).
- Preliminary line counts (non-blank, non-# lines; final numbers come from central's scripts/docs/loc.py): service.py 286, native.py 196 (E7 tooling), client v6_flhub/__init__.py 36, Dockerfile 16.
- Next: wait for central's S3 R1-skeleton and the framework-guide template; draft docs/frameworks/vantage6.md meanwhile in /tmp.

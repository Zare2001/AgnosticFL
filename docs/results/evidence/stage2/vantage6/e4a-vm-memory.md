Source: sysmon summaries on the vantage6 VM, `~/flhub-runtime/sysmon/summaries/sysmon-vantage6-s2-e4a-{vantage6,mixed}-<N>mb.json`, written during the runs by `bash deploy/vantage6-vm/sysmon.sh summary <run> <from> <to>` (`scripts/sysmon.py summarize`, 1 s samples); this table reads their fields `system.mem_used_mb_peak`, `system.mem_used_mb_mean`, `system.cpu_mean`, `system.cpu_peak` and `procs.<component>.rss_mb_peak` (read on 2026-10-14).

# E4a: vantage6 VM memory and CPU per payload size

Values in MiB: sysmon reads `/proc/meminfo` in KiB and divides by 1024, and computes RSS as pages × page size / 2^20. The VM (4 vCPU, 31 GiB RAM) hosts the two vantage6 hospitals `v6-h3` and `v6-h4`. `v6-algorithm` is the sum over all running training containers of both hospitals; a node is that hospital's node process. Windows are the runs' start and end in UTC on 2026-10-13. A `payload-<N>` model has N MiB. The vantage6 256 MiB run and the mixed 128 MiB run have no summary.

| run | window (UTC) | samples | VM memory used, peak | VM memory used, mean | training containers (v6-algorithm), peak RSS | v6-h3 node, peak RSS | v6-h4 node, peak RSS | dockerd, peak RSS | CPU mean % | CPU peak % |
|---|---|---|---|---|---|---|---|---|---|---|
| s2-e4a-vantage6-1mb | 16:19:08–16:19:29 | 21 | 1,791.9 | 1,479.7 | 585.2 | 122.5 | 122.0 | 228.3 | 23.4 | 58.1 |
| s2-e4a-vantage6-8mb | 16:19:32–16:20:12 | 40 | 1,973.1 | 1,625.7 | 638.5 | 261.5 | 260.8 | 238.0 | 18.4 | 71.8 |
| s2-e4a-vantage6-32mb | 16:20:18–16:23:21 | 183 | 2,361.9 | 1,790.5 | 705.2 | 374.4 | 443.7 | 237.9 | 9.2 | 63.6 |
| s2-e4a-vantage6-64mb | 16:23:25–16:29:29 | 364 | 3,321.8 | 2,250.9 | 1,406.2 | 1,024.9 | 841.0 | 232.2 | 7.9 | 79.2 |
| s2-e4a-vantage6-128mb | 16:29:36–16:54:39 | 1503 | 5,281.7 | 2,878.6 | 2,134.0 | 1,588.0 | 1,581.5 | 241.4 | 6.7 | 68.8 |
| s2-e4a-mixed-1mb | 16:55:31–16:55:52 | 21 | 2,334.6 | 1,847.5 | 567.2 | 646.0 | 516.0 | 230.4 | 22.9 | 58.9 |
| s2-e4a-mixed-8mb | 16:56:29–16:57:12 | 43 | 2,129.4 | 1,791.2 | 596.3 | 409.2 | 265.4 | 241.5 | 16.0 | 71.8 |
| s2-e4a-mixed-32mb | 16:57:55–17:01:02 | 187 | 2,506.0 | 1,905.6 | 406.1 | 518.1 | 393.0 | 229.2 | 8.9 | 67.7 |
| s2-e4a-mixed-64mb | 17:01:44–17:07:50 | 366 | 3,442.8 | 2,326.0 | 1,199.3 | 865.2 | 688.0 | 228.9 | 7.1 | 63.4 |

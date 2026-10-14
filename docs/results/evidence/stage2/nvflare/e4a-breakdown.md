# NVFlare E4a breakdown (final runs, medians over rounds 2-3)

Source: central's run dirs (metrics.jsonl) of the final runs in runs.md; script ~/flhub-analysis/tools/e4a_breakdown.py (nvflare VM). Per site: t_result = relay prep (hub-core read + FLModel) + NVFlare reply (task sent -> reply at the server) + store (hub-core write) + rest (adapter queue/polls, envelope); 'nvflare_transfer' = reply - train_time - data_load (NVFlare transport both ways + client framework).

## Scope nvflare (nvf-h1, nvf-h2)

| MB | round_s | fanout_s | t_result_s | train_s | prep_s | nvflare_transfer_s | store_s | rest_s | agg_s | rounds |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 2.523 | 2.515 | 2.517 | 0.0 | 0.003 | 2.356 | 0.005 | 0.154 | 0.008 | 2 |
| 8 | 2.669 | 2.641 | 2.643 | 0.002 | 0.017 | 2.39 | 0.024 | 0.218 | 0.016 | 2 |
| 32 | 2.664 | 2.515 | 2.391 | 0.005 | 0.092 | 1.95 | 0.13 | 0.204 | 0.052 | 2 |
| 64 | 3.675 | 3.396 | 3.273 | 0.013 | 0.173 | 2.562 | 0.256 | 0.269 | 0.102 | 2 |
| 128 | 5.083 | 4.53 | 4.279 | 0.022 | 0.368 | 2.882 | 0.516 | 0.239 | 0.188 | 2 |
| 256 | 10.293 | 9.194 | 8.692 | 0.048 | 0.668 | 6.287 | 1.021 | 0.592 | 0.376 | 2 |

## Scope mixed (NVFlare columns; the round is set by vantage6)

| MB | round_s | fanout_s | t_result_s | train_s | prep_s | nvflare_transfer_s | store_s | rest_s | agg_s | rounds |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 6.464 | 1.781 | 1.782 | 0.0 | 0.003 | 1.607 | 0.004 | 0.168 | 0.019 | 2 |
| 8 | 13.314 | 2.001 | 2.003 | 0.001 | 0.017 | 1.822 | 0.025 | 0.145 | 0.039 | 2 |
| 32 | 58.889 | 2.898 | 2.773 | 0.004 | 0.056 | 2.477 | 0.088 | 0.173 | 0.111 | 2 |
| 64 | 116.2 | 3.789 | 3.538 | 0.007 | 0.15 | 2.932 | 0.26 | 0.287 | 0.238 | 2 |

Floor: below ~64 MB an NVFlare round costs ~2.3-2.6 s regardless of size. Client log of s2-e4a-nvflare-1mb: task in -> result out 0.55 s at the client, result out -> next task in 2.04 s. Cause: the NVFlare server tells clients to wait task_request_interval (default 2 s, private/fed/server/server_json_config.py:102; WAIT_TIME header, server_runner.py:260/365) before the next task request. Server-side job-config knob (server.task_request_interval), latency only; not changed in the campaign (frozen), applies equally to the E7 native runs. 512 MB not run: central testbed disk limit; mixed stopped at 128 MB (disk).

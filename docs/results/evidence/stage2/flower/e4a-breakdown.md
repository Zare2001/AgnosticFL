# E4a — Flower round-time breakdown (stage 2, final runs)

Source: the run directories (`metrics.jsonl`) of the final runs `s2-e4a-flower-<S>mb` (S = 1, 8, 32, 64, 128, 256), copied from the central host to the Flower hospital VM. Command: `python3 -I deploy/flower-vm/analysis/e4a_breakdown.py <directory with the run directories>`. The 512 MiB run of the later sweep extension is not included (its run directory was not copied).

Medians over rounds 2–3 and both sites (`flw-h5`, `flw-h6`), in seconds. `MB` in the header is the payload `payload-<S>` in MiB per site and direction. Columns: `round_s` round time; `fanout_s` Flower fan-out (dispatch to the last Flower result); `t_result_s` dispatch to a site's result; the relay's timing metrics `prep_s` (job to model ready), `push_s` (TRAIN message to the SuperLink), `reply_s` (push to reply pulled) and `store_s` (reply to weights in the hub core); `flower_s` = reply − training − data loading (SuperNode download, ClientApp start, upload, pull); `train_s` echo training; `rest_s` = t_result − prep − push − reply − store (adapter and hub-core side outside the relay); `agg_s` hub aggregation; `rounds` number of rounds in the median.

| MB | round_s | fanout_s | t_result_s | prep_s | push_s | reply_s | flower_s | train_s | store_s | rest_s | agg_s | rounds |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 5.79 | 5.79 | 5.28 | 0.0 | 0.07 | 5.13 | 5.11 | 0.0 | 0.01 | 0.08 | 0.01 | 2 |
| 8 | 6.07 | 6.04 | 5.04 | 0.02 | 0.14 | 4.72 | 4.69 | 0.0 | 0.03 | 0.12 | 0.01 | 2 |
| 32 | 7.69 | 7.55 | 6.29 | 0.09 | 0.47 | 5.5 | 5.47 | 0.01 | 0.14 | 0.14 | 0.05 | 2 |
| 64 | 9.08 | 8.8 | 7.93 | 0.17 | 0.82 | 6.51 | 6.46 | 0.03 | 0.27 | 0.13 | 0.1 | 2 |
| 128 | 13.51 | 12.96 | 12.58 | 0.24 | 1.61 | 10.1 | 10.03 | 0.05 | 0.53 | 0.21 | 0.2 | 2 |
| 256 | 20.61 | 19.51 | 17.75 | 0.37 | 3.35 | 12.87 | 12.75 | 0.1 | 1.06 | 0.12 | 0.39 | 2 |

Flower's own path (`flower_s`) has a fixed part of about 5 s and grows by about 0.03 s per MiB (5.11 s at 1 MiB, 12.75 s at 256 MiB). The hub-side medians (prep + push + store + rest) add up to 0.16 s at 1 MiB and 4.90 s at 256 MiB.

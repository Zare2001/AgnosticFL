# NVFlare E7: native FedAvg vs hub path (final runs)

Run data: the run directories on the central machine; script `deploy/nvflare-vm/analysis/e7_compare.py`; weight differences from the finalize step (`native_vs_hub.json`). Native round = FedAvg send_model -> aggregated model; hub round = dispatch -> envelopes -> aggregated (round/duration_s + round/agg_s); hub total adds evaluation and checkpointing. Medians/IQR over rounds >= 2.

## C1: cnn

| round | native s | hub s (dispatch..aggregated) | hub total s (+eval) | global max abs diff | one-step aggregation diff |
|---|---|---|---|---|---|
| 1 | 4.502 | 2.773 | 4.833 | 2.9802322387695312e-08 | None |
| 2 | 3.502 | 3.276 | 5.349 | 0.0006298845401033759 | None |
| 3 | 3.002 | 3.022 | 5.049 | 0.03387218713760376 | None |
| 4 | 3.502 | 3.022 | 5.068 | 0.122465412132442 | None |
| 5 | 3.502 | 3.525 | 5.571 | 0.11064250767230988 | None |
| 6 | 3.002 | 3.028 | 5.04 | 0.1461526956409216 | None |
| 7 | 3.502 | 3.025 | 5.06 | 0.17543229460716248 | None |
| 8 | 3.502 | 3.526 | 5.562 | 0.20046308636665344 | None |
| 9 | 3.002 | 3.526 | 5.541 | 0.21657761931419373 | None |
| 10 | 4.002 | 3.023 | 4.975 | 0.23239514231681824 | None |
| 11 | 4.003 | 3.274 | 5.251 | 0.2651771008968353 | None |
| 12 | 3.502 | 3.526 | 5.565 | 0.27350103855133057 | None |
| 13 | 3.002 | 3.026 | 4.965 | 0.27597129344940186 | None |
| 14 | 3.002 | 3.023 | 4.999 | 0.5531820058822632 | None |
| 15 | 3.502 | 3.527 | 5.564 | 0.5531820058822632 | None |
| 16 | 3.002 | 3.276 | 5.319 | 0.5530717968940735 | None |
| 17 | 3.002 | 3.276 | 5.334 | 0.5587087273597717 | None |
| 18 | 4.002 | 3.526 | 5.557 | 0.5997756123542786 | None |
| 19 | 3.002 | 3.022 | 5.066 | 0.6150527177378535 | None |
| 20 | 3.502 | 3.023 | 5.059 | 0.6795942352619022 | None |

{
 "native": {
  "median": 3.502,
  "iqr": 0.5,
  "n": 19
 },
 "hub": {
  "median": 3.274,
  "iqr": 0.503,
  "n": 19
 },
 "hub_total": {
  "median": 5.251,
  "iqr": 0.508,
  "n": 19
 },
 "overhead_s": -0.228,
 "overhead_pct": -6.5,
 "native_vs_hub_max_diff": 0.6795942352619022,
 "max_one_step_aggregation_diff": null
}

## C2: payload

| round | native s | hub s (dispatch..aggregated) | hub total s (+eval) | global max abs diff | one-step aggregation diff |
|---|---|---|---|---|---|
| 1 | 3.521 | 3.634 | 4.053 | None | None |
| 2 | 2.517 | 2.535 | 2.91 | None | None |
| 3 | 3.018 | 2.795 | 3.155 | None | None |
| 4 | 2.517 | 2.489 | 2.839 | None | None |
| 5 | 2.508 | 2.705 | 3.088 | 2.2351741790771484e-08 | None |

{
 "native": {
  "median": 2.517,
  "iqr": 0.382,
  "n": 4
 },
 "hub": {
  "median": 2.62,
  "iqr": 0.272,
  "n": 4
 },
 "hub_total": {
  "median": 2.999,
  "iqr": 0.282,
  "n": 4
 },
 "overhead_s": 0.103,
 "overhead_pct": 4.1,
 "native_vs_hub_max_diff": 2.2351741790771484e-08,
 "max_one_step_aggregation_diff": null
}

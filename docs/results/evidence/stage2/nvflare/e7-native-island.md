# NVFlare E7 native FedAvg — island dry run (nvflare VM, 2026-10-13)

`adapters/nvflare/native.py` (NativeFedAvg = NVFlare 2.9.0 `FedAvg` + recording hooks) on the island
(localhost server, island clients nvf-h1/nvf-h2 with the job's flhub_core 0.2.0+fe357a1dbec1), running next
to the idle relay job (server `max_jobs` 4, clients without GPU limits).

| run | config | job | result |
|---|---|---|---|
| native-cnn-r2 | cnn-small, organamnist-iid, 3 rounds, lr 0.05 | FINISHED:COMPLETED, 21.5 s | client weights == local `flhub_core.train` from the same global (max abs diff 0.0, all rounds); one-step aggregation difference hub `fedavg` (float64) vs NVFlare FedAvg (float32, InTime) on the same client results: 2.98e-08, 5.96e-08, 2.98e-08 (max rel 2.5e-05), 0/8 tensors bit-identical; round 3.0-4.0 s |
| native-payload-r1 (via `deploy/central/nvflare/native/run.sh`) | payload-8 echo, 3 rounds | FINISHED:COMPLETED, 19.5 s | metrics.jsonl with per-site t_result_s, train_time_s, overhead_s; client weights not kept (hashes only) |

Findings: (1) FedAvg replaces the model meta after each round, so the hub train_config is re-attached in
`send_model()`; (2) a server app without custom code is held to the kit's class allow-list (ComponentNotAuthorized),
so the job ships the workflow module as BYOC server code; (3) running the builder as `python -m` would serialize the
class as `__main__`-derived `native.NativeFedAvg`, so the job is built from the package import.

## Native vs hub with the E7 configuration (dry run, 3 rounds, island)

`configs/stage2/e7/s2-e7-nvflare-hub-cnn.yaml` (organamnist-iid, cnn-small, lr 0.1, nvf-h1 n=5761, nvf-h2 n=5760):
native `s2-e7-nvflare-native-cnn` (native/run.sh path, `--rounds 3`) vs hub orchestrator v2 (`--only nvflare --rounds 3`).
`python -m adapters.nvflare.native compare` (`e7-native-vs-hub-island-dry.json`):

| round | global max abs diff native vs hub | one-step aggregation diff (hub fedavg of the native clients vs native global) |
|---|---|---|
| 0 | 0.0 (same init) | — |
| 1 | 2.98e-08 | 2.98e-08 |
| 2 | 6.30e-04 | 5.96e-08 |
| 3 | 3.39e-02 | 5.96e-08 |

The aggregation itself differs only at float32 rounding level (NVFlare FedAvg: float32, weights 5761/5760 not
exactly representable; hub: float64, cast once). Local SGD (lr 0.1) amplifies that perturbation from round 2 on, so
the two trajectories drift apart; E7 compares them by the per-round diff plus final test metrics.

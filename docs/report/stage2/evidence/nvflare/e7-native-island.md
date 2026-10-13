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

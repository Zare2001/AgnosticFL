# Aggregators

The hub aggregates on the server side, so the aggregation method is a hub setting: changing it needs no change and no restart at the hospitals. Six aggregators are built in ([`hub/aggregators/`](../hub/aggregators/)); the orchestrator and the simulation use the same code, so a real run stays hash-identical to its simulation for every aggregator.

## Overview

With the participating sites k, their example counts n_k, p_k = n_k / Σ n_j, the global model x and the pseudo-gradient Δ = Σ_k p_k (w_k − x):

| Name | Update | Server state | Parameters (defaults) |
|---|---|---|---|
| `fedavg` | x ← Σ_k p_k w_k | none | none |
| `fedprox` | as `fedavg`; the sites add μ(w − x) to their gradient | none | `mu` (required) |
| `fedavgm` | m ← β m + Δ; x ← x + η m | momentum m | `eta` 1.0, `beta` 0.9 |
| `fedadam` | m ← β₁ m + (1 − β₁) Δ; v ← β₂ v + (1 − β₂) Δ²; x ← x + η m / (√v + τ) | m, v (v₀ = τ²) | `eta` 0.01, `beta1` 0.9, `beta2` 0.99, `tau` 1e-3 |
| `fedyogi` | as `fedadam`, with v ← v − (1 − β₂) Δ² sign(v − Δ²) | m, v | as `fedadam` |
| `fedadagrad` | v ← v + Δ²; x ← x + η Δ / (√v + τ) | v (v₀ = τ²) | `eta` 0.1, `tau` 1e-3 |

All arithmetic and all state are float64, the sites are summed in site-id order (arrival order never matters), and the new global model is cast to float32 once per round. `fedavg` is exactly the stage-1 implementation. The state is part of every round's checkpoint (its sha256 is in the round record), so `--resume` continues with it bit-exactly.

**In a run configuration:**

```yaml
aggregator: {name: fedavgm, params: {eta: 1.0, beta: 0.9}}
```

`fedprox` passes `mu` to the sites as `train_config["prox_mu"]`, where `flhub_core.train` adds the proximal term; the hospitals' client code stays the same because the configuration travels unchanged ([client contract](client-contract.md)).

## Tuned values (stage 2)

Tuned once in the simulation: OrganAMNIST, label-skewed partition `dir0.3`, six sites, `cnn-small`, 30 rounds, seed 2026, selected by final validation accuracy; the client learning rate first with `fedavg`, then each server parameter ([tuning.md](results/evidence/stage2/tuning.md), [`configs/stage2/tuned.yaml`](../configs/stage2/tuned.yaml)).

| Setting | Grid | Chosen | Final validation accuracy |
|---|---|---|---|
| client learning rate (`fedavg`) | 0.01, 0.03, 0.05, 0.1 | **0.1** | 0.9037 |
| `fedavgm` η (β 0.9) | 0.1, 0.3, 1.0 | **1.0** | 0.9137 (best overall) |
| `fedadam` η | 0.001, 0.003, 0.01, 0.03 | **0.03** | 0.9059 |
| `fedyogi` η | 0.001, 0.003, 0.01, 0.03 | **0.03** | 0.8789 |
| `fedadagrad` η | 0.01, 0.03, 0.1 | **0.03** | 0.8848 |
| `fedprox` μ | 0.001, 0.01, 0.1 | **0.01** | 0.9029 |

Four optima lie on the edge of the grid (client learning rate, `fedavgm`, `fedadam`, `fedyogi`), and differences of about one point between the best settings are within what seeds can change. With β = 0.9, `fedavgm` with η = 1.0 is an effective server step of η / (1 − β) = 10.

## Results

On the test split, mean of three seeds ([stage-2 results](results/stage2.md#e2-aggregators)):

- **Label skew:** `fedavgm` (80.2 % on `dir0.3`) and `fedyogi` (78.4 % on `dir0.1`) gain 2–4 points over `fedavg` (78.0 %, 74.7 %), and up to 5 points on the worst site.
- **IID data:** no gain; the server steps tuned for the skewed case reach the 80.5 % target later (`fedavgm` 27 rounds, `fedyogi` 19, against 16 for `fedavg`).
- **`fedprox`** with μ = 0.01 stays within 0.4 points of `fedavg`; **`fedadagrad`** is the weakest server optimizer here.

## Divergence findings

- **`fedavgm`'s tuned step is too large outside the setting it was tuned for.** With the wider `cnn-medium` it reached 50 % at round 7 and collapsed to chance level (11.1 %) from round 9; with half of the sites per round it diverged from round 24 and ended at 9.8 %, while `fedavg` reached 66.1 % with the same participants. A diagnostic simulation with η = 0.3 and the same participants stayed stable (73.9 %, above `fedavg`). With `resnet18-gn` the same step did not diverge and sped up the slow start (50.7 % against 42.7 % after 20 rounds).
- **`fedadam` diverged in one of three seeds** on `dir0.3`: 81 % until round 45, then 16.2 % at round 50, with the test loss rising from round 39. This is the known instability of Adam-type server steps when the second moment becomes small; `fedyogi`, whose second-moment update is additive, stayed stable on the same partition and seeds.
- **Every collapse is reproduced bit for bit by the simulation**, so it is the optimizer, not the system.

Tune server learning rates per model size and per participation level; the stage-2 grid covered only the small CNN with full participation ([limitations](limitations-and-future-work.md)).

## Adding an aggregator

1. Subclass `Aggregator` in [`hub/aggregators/strategies.py`](../hub/aggregators/strategies.py): set `name`, `defaults` (and `required` parameters), implement `init(global_weights)` (the initial state: float64 arrays and int or float scalars) and `aggregate(global_weights, results, state, rnd)` → `(new_global, new_state)`. Use `pseudo_gradient()` for Δ (float64, site-id order) and cast the new model to float32 once, as the existing classes do.
2. If the sites need a setting, return it from `client_config()`; it is added to the `train_config`. A new client-side key also needs support in `flhub_core.train`, which means a new `flhub_core` version and its rollout to the hospitals.
3. Add the class to `REGISTRY`; configurations then select it by name.
4. Keep it deterministic: no randomness that is not derived from the run's seed and the round, and no dependence on arrival order. Then real runs stay hash-identical to the simulation and resume stays bit-exact.
5. Add tests next to the existing ones in [`tests/unit/test_aggregators.py`](../tests/unit/test_aggregators.py): the formula on small arrays, a state round trip that resumes bit-exactly, unknown parameters.

Not built in: robust aggregators (median, trimmed mean, Krum) and methods with client-side control variates such as SCAFFOLD, which would also need a client change ([support matrix](support-matrix.md)).

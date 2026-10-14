# Client contract

What every hospital-side client must do so that the hub can aggregate its updates with those of other frameworks. The contract is the same for every framework; only the transport differs. The three example clients implement it in each framework's standard pattern:

| Framework | Example | Pattern | Guide |
|---|---|---|---|
| NVFlare | [`clients/nvflare/client.py`](../clients/nvflare/client.py) | Client API loop (`flare.receive` / `flare.send`) | [NVFlare §3](frameworks/nvflare.md#3-client-side-what-the-hospital-code-must-contain) |
| vantage6 | [`clients/vantage6/v6_flhub/__init__.py`](../clients/vantage6/v6_flhub/__init__.py) | algorithm function with `@data(1)`, `@metadata`, run by `wrap_algorithm()` | [vantage6 §3](frameworks/vantage6.md#3-client-side-what-the-hospital-code-must-contain) |
| Flower | [`clients/flower/flhub_flower/client_app.py`](../clients/flower/flhub_flower/client_app.py) | ClientApp with `@app.train()` and `@app.query()` | [Flower §3](frameworks/flower.md#3-client-side-what-the-hospital-code-must-contain) |

## What the client receives

| Item | Content | NVFlare | vantage6 | Flower |
|---|---|---|---|---|
| Global weights | float32 tensors, keyed by the model's `state_dict` names | `FLModel.params` (`FULL`, numpy) | `weights_b64`: base64 safetensors in the task input | `ArrayRecord` `"arrays"` |
| Training configuration | flat scalars: `local_epochs`, `batch_size`, `lr`, `momentum`, `seed`, the hub `round`, and optionally `model`, `data_variant`, `prox_mu`, `weight_decay` | `FLModel.meta["train_config"]`; round = `FLModel.current_round` | `train_config` in the task input (round included) | `ConfigRecord` `"config"` (round included) |
| Data variant | `train_config["data_variant"]`, e.g. `organamnist-dir0.3`; absent = the site's default data | inside `train_config` | the adapter selects the node database with that label | inside `train_config` |
| Site identity | the hub's site id | the NVFlare client name | the organisation, mapped with the `sites` input | the SuperNode's node config `site-id` |

Pass the configuration on **unchanged**: the hub hashes it, and the shared library decides what each key means. New keys (for example FedProx's `prox_mu`) then reach the training code without a client change.

## What the client runs

Exactly one call, with the received weights, the site's data and the configuration:

```python
from flhub_core.data import load_site
from flhub_core.train import train

new_weights, num_examples, metrics = train(weights, load_site(path_or_dataframe), config)
```

`load_site` takes a file path (NVFlare, Flower) or the DataFrame a vantage6 node passes in. Do not preprocess the data, change threads, seeds or dtypes, or wrap the model: `flhub_core.train` sets one CPU thread, deterministic algorithms, a fresh optimizer and a data order seeded by (seed, round, data fingerprint), so that every framework produces the same update ([architecture](architecture.md#determinism-and-flhub_core)).

## What the client returns

| Item | Content | NVFlare | vantage6 | Flower |
|---|---|---|---|---|
| New weights | all tensors, float32, same names and shapes | `FLModel(params_type=FULL, params=...)` | `weights_b64` in the result dict | `ArrayRecord` `"arrays"` |
| `num_examples` | the integer from `train`, > 0 (the FedAvg weight) | `meta["num_examples"]` (and `NUM_STEPS_CURRENT_ROUND`) | `num_examples` | `MetricRecord["num-examples"]` |
| Metrics | the dict from `train`, numbers only: `train_loss`, `train_acc`, `train_time_s`, `data_load_s`, `peak_rss_mb`, `data_fp` | `FLModel.metrics` | `metrics` | the rest of `MetricRecord` `"metrics"` |
| Code version | `flhub_core.code_version()`: version + hash of the library's source files | `meta["client_code_version"]` | `client_code_version` | `ConfigRecord` `"site-info"`, key `client-code-version` |

`data_fp` (32 bits of the fingerprint of the data the site trained on) lets the hub check that a site used the intended data variant. `client_code_version` lets it check that every site ran byte-identical training code; with `strict_code_version: true` in the run configuration, the hub rejects updates from other code. The adapter adds the envelope fields the hospital does not need to know (round, base model, configuration hash; [protocol](protocol.md) §3).

## Determinism requirements

- Run `flhub_core` as shipped by the operator, byte-identical (its `code_version` must match the hub's).
- Use the pinned versions of torch, numpy and safetensors ([`deploy/versions.env`](../deploy/versions.env)); `python -m flhub_core.selfcheck` must print the same digest as on the hub.
- Keep the defaults of `flhub_core.train`: it needs no hospital-side settings beyond the data location.

Without this, aggregation still works, but the real run no longer equals the simulation bit for bit, and the equivalence check falls back to a tolerance.

## Data layout and data variants

- **Files** (NVFlare, Flower): one CSV per site, `label,x0..x783` with integer pixels, at `$FLHUB_DATA_DIR/<site>.csv` (default directory `~/flhub-data`) or a configured path (NVFlare: `FLHUB_DATA_PATH`; Flower: node config `data-path`). A data variant is a file `<variant>-<site>.csv` next to it; `load_site` returns a lazy handle and resolves the variant per round, so a new variant needs a new file, never a configuration change.
- **Node databases** (vantage6): one labelled CSV database per variant in the node configuration (label = the variant with every character outside `[A-Za-z0-9_]` replaced by `_`, plus `default`); the node passes the selected database as a DataFrame.
- **Benchmark data**: `python -m flhub_core.data materialize --sites <site> ...` writes the test partitions with pinned sha256 (stage-2 variants: `--variants all --manifests configs/stage2/partitions`).

## How new library versions reach the hospitals

Through each framework's normal code channel, with no hospital step:

| Framework | Channel | Hospital requirement |
|---|---|---|
| NVFlare | the relay job's custom code (`custom/client.py`, `custom/flhub_core/`) | do **not** install `flhub_core`: NVFlare appends `custom/` to `sys.path`, so an installed copy would shadow the job's |
| vantage6 | a new algorithm image tag, pulled by the node at its first task | the node policy allows the operator's image |
| Flower | the FAB of the relay run (`flhub_core` packed next to the ClientApp, first on `sys.path`) | none |

In the stage-2 rollout of `flhub_core` 0.2, this took one client restart per NVFlare hospital (to remove an installed copy, 50.3 s offline) and no step at the vantage6 and Flower hospitals; every later version needs none ([hospital ledger](results/evidence/hospital-ledger.md), [cost and efficiency](cost-and-efficiency.md#3-integration-cost-at-the-hospitals)).

## Checklist

A client for a new framework must:

- [ ] use that framework's standard client pattern and its normal code distribution;
- [ ] receive the global weights as float32 tensors and the configuration including the round;
- [ ] call `flhub_core.train(weights, flhub_core.data.load_site(data), config)` with the configuration unchanged;
- [ ] return the new weights, `num_examples`, the metrics and `flhub_core.code_version()`;
- [ ] identify itself with the site id the hub uses;
- [ ] pass the conformance suite through its adapter ([adding a framework](adding-a-framework.md)).

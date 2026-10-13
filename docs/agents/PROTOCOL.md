# flhub/0.1 — adapter protocol

The translation layer between the framework-free orchestrator and one framework. Inspired by MCP: one small, versioned protocol; each framework gets an adapter that speaks it; the orchestrator never contains framework code. Changes need an ADR and a version bump (`flhub/0.2`), announced on the bus.

## 1. Roles

- **Orchestrator** (hub): owns rounds, FedAvg, evaluation. Calls adapters.
- **Hub core**: HTTP service on `127.0.0.1:8800` with the blob store and model registry. Used by orchestrator and adapters.
- **Adapter**: HTTP service on `127.0.0.1:881x`, method `POST /rpc`, JSON-RPC 2.0. Wraps exactly one framework server. Stateless towards the orchestrator except for task ids.

## 2. Methods

All requests: `{"jsonrpc":"2.0","id":<int>,"method":"<name>","params":{...}}`. Every `params` carries `"protocol":"flhub/0.1"`; adapters reject unknown versions with error `-32010`.

### `discover` → adapter description
```json
{"adapter":"nvflare","framework":"nvflare","framework_version":"2.x.y","protocol":"flhub/0.1",
 "sites":[{"site_id":"nvf-h1","status":"online"},{"site_id":"nvf-h2","status":"online"}],
 "capabilities":{"payload":"full_weights","dtypes":["float32"],"max_payload_mb":64,
                 "evaluate":false,"min_round_interval_s":0}}
```
`status` ∈ `online | offline | unknown`. An adapter is ready only when all its sites are `online`.

### `dispatch` → start one training round on some or all of the adapter's sites
params:
```json
{"protocol":"flhub/0.1","round":7,
 "model":{"ref":"sha256:9f2c…","url":"http://127.0.0.1:8800/blobs/sha256:9f2c…"},
 "train_config":{"local_epochs":1,"batch_size":64,"lr":0.05,"momentum":0.0,"seed":2026},
 "sites":["nvf-h1","nvf-h2"],"deadline_s":1800}
```
result: `{"task_id":"nvflare-r7-…"}`. Must return quickly (< 5 s); the work runs asynchronously.

### `tasks/get` → task state
result: `{"task_id":"…","state":"queued|running|completed|failed|cancelled","progress":{"done":1,"total":2},"error":null}`

### `collect` → envelopes of a completed task
result: `{"task_id":"…","envelopes":[<Envelope>, …]}`. Valid only in state `completed`. Idempotent.

### `cancel` → best-effort cancel
result: `{"task_id":"…","state":"cancelled"}`

### Errors
JSON-RPC error codes: `-32010` unsupported protocol, `-32011` unknown site, `-32012` task not found, `-32013` task not completed, `-32014` framework error (message must include the framework's own error text), `-32015` adapter busy (a task is already running for these sites).

## 3. Envelope (one per site per round)

```json
{"protocol":"flhub/0.1","round":7,"site_id":"v6-h3","framework":"vantage6",
 "base_model":"sha256:9f2c…",
 "weights":"sha256:41ab…",
 "num_examples":2834,
 "metrics":{"train_loss":0.412,"train_acc":0.861},
 "train_config_hash":"sha256:…",
 "client_code_version":"<git sha of flhub_core used by the client>",
 "created_at":"2026-10-14T09:12:03Z"}
```

The orchestrator rejects an envelope if: `base_model` ≠ the round's global model; `weights` blob missing; tensor names/shapes/dtypes differ from the registered model spec; `num_examples` ≤ 0; `site_id` not expected for this round.

## 4. Hub core HTTP API (`127.0.0.1:8800`)

| Method | Path | Purpose |
|---|---|---|
| PUT | `/blobs/sha256:<hex>` | store bytes; server verifies the hash |
| GET | `/blobs/sha256:<hex>` | fetch bytes |
| POST | `/models/spec` | register model spec (names, shapes, dtypes) |
| GET | `/models/spec` | fetch model spec |
| GET | `/health` | liveness |

Weights blobs are `safetensors` files of float32 tensors keyed by the `flhub_core` model's `state_dict` names.

## 5. Mapping per framework (what each adapter translates)

| flhub | NVFlare | vantage6 | Flower |
|---|---|---|---|
| dispatch(round, model) | relay `ModelController.run()` gets the job, `send_model_and_wait(targets, FLModel(params, params_type=FULL, current_round))` | `client.task.create(..., input_={"method":"train","kwargs":{weights_b64, train_config}})` to the 2 orgs | relay `ServerApp`: `Message(content=RecordDict({"arrays":ArrayRecord, "config":ConfigRecord}), message_type=TRAIN, dst_node_id=…)` via `grid.send_and_receive` |
| site_id | NVFlare client name | organisation name (or node name) | SuperNode `--node-config site-id=…` echoed in the reply |
| num_examples | `FLModel.meta["NUM_STEPS_CURRENT_ROUND"]` or a `num_examples` meta key | returned in the algorithm result dict | `MetricRecord["num-examples"]` |
| weights back | `FLModel.params` (FULL) | base64 safetensors in the result | `ArrayRecord` |
| sites online | connected clients (FLARE API `get_system_info`) | node status via vantage6 client | `grid.get_node_ids()` |

Verify the exact API names for the installed versions before coding; update this table if they differ.

## 6. Adding a new framework (BranchKey, others)

Implement the 5 methods above in a service on a free local port, keep the hospital side standard for that framework, and pass `tests/conformance/`. Nothing in the orchestrator or hub core may change.

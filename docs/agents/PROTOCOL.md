# flhub/0.1 — adapter protocol

**Status: FROZEN 2026-10-13 (central).** Reference implementation: `adapters/common/` (server side) and `adapters/common/rpcclient.py` (orchestrator side).

The translation layer between the framework-free orchestrator and one framework. Inspired by MCP: one small, versioned protocol; each framework gets an adapter that speaks it; the orchestrator never contains framework code. Changes need an ADR and a version bump (`flhub/0.2`), announced on the bus.

## 1. Roles

- **Orchestrator** (hub): owns rounds, FedAvg, evaluation. Calls adapters.
- **Hub core**: HTTP service on `127.0.0.1:8800` with the blob store and model registry. Used by orchestrator and adapters.
- **Adapter**: HTTP service on `127.0.0.1:881x`, method `POST /rpc`, JSON-RPC 2.0, plus `GET /health`. Wraps exactly one framework server. Stateless towards the orchestrator except for task ids. One adapter serves a fixed list of sites (its configuration).

## 2. Methods

All requests: `{"jsonrpc":"2.0","id":<int>,"method":"<name>","params":{...}}`. Every `params` carries `"protocol":"flhub/0.1"`; adapters reject unknown versions with error `-32010`. Responses always use HTTP 200 with a JSON-RPC `result` or `error`.

### `discover` → adapter description
```json
{"adapter":"nvflare","framework":"nvflare","framework_version":"2.x.y","protocol":"flhub/0.1",
 "sites":[{"site_id":"nvf-h1","status":"online"},{"site_id":"nvf-h2","status":"online"}],
 "capabilities":{"payload":"full_weights","dtypes":["float32"],"max_payload_mb":64,
                 "evaluate":false,"min_round_interval_s":0}}
```
`sites` lists every site the adapter serves. `status` ∈ `online | offline | unknown` (`unknown`: the adapter cannot tell right now, e.g. its relay is not running). An adapter is ready only when all its sites are `online`.

### `dispatch` → start one training round on some or all of the adapter's sites
params:
```json
{"protocol":"flhub/0.1","round":7,
 "model":{"ref":"sha256:9f2c…","url":"http://127.0.0.1:8800/blobs/sha256:9f2c…"},
 "train_config":{"local_epochs":1,"batch_size":64,"lr":0.05,"momentum":0.0,"seed":2026},
 "sites":["nvf-h1","nvf-h2"],"deadline_s":1800}
```
- `round`: int ≥ 0. `model.ref` is required (the adapter fetches the blob from the hub core); `model.url` is informational.
- `train_config`: the five keys above are required; adapters pass it through unchanged.
- **Client config**: what a hospital client receives is `train_config` plus `"round": <round>`, e.g. `{"local_epochs":1,…,"seed":2026,"round":7}`. Clients hand it unchanged to `flhub_core.train.train(weights, data, cfg)`.
- `sites`: non-empty subset of the adapter's sites, no duplicates (default: all). Unknown → `-32011`.
- `deadline_s` (default 1800): after it the task becomes `failed` ("deadline … exceeded").

result: `{"task_id":"nvflare-r7-…"}`. Must return quickly (< 5 s); the work runs asynchronously. A task still `queued`/`running` for any of the same sites → `-32015`.

### `tasks/get` → task state
params `{"task_id"}`; result: `{"task_id":"…","state":"queued|running|completed|failed|cancelled","progress":{"done":1,"total":2},"error":null}`. For `failed`, `error` is a string that includes the framework's own error text.

### `collect` → envelopes of a completed task
params `{"task_id"}`; result: `{"task_id":"…","envelopes":[<Envelope>, …]}`, one per dispatched site, in dispatch order. Valid only in state `completed` (else `-32013`, with `data.error` when failed). Idempotent.

### `cancel` → best-effort cancel
params `{"task_id"}`; result: `{"task_id":"…","state":"cancelled"}`. On a task that already finished it changes nothing and returns its current state.

### Errors
JSON-RPC error codes: `-32700` parse error, `-32600` invalid request, `-32601` unknown method, `-32602` invalid params, `-32603` internal error, `-32010` unsupported protocol, `-32011` unknown site, `-32012` task not found, `-32013` task not completed, `-32014` framework error (message must include the framework's own error text), `-32015` adapter busy (a task is already running for these sites).

## 3. Envelope (one per site per round)

```json
{"protocol":"flhub/0.1","round":7,"site_id":"v6-h3","framework":"vantage6",
 "base_model":"sha256:9f2c…",
 "weights":"sha256:41ab…",
 "num_examples":785,
 "metrics":{"train_loss":0.412,"train_acc":0.861},
 "train_config_hash":"sha256:…",
 "client_code_version":"0.1.0+c739683bab25",
 "created_at":"2026-10-14T09:12:03Z"}
```

- `base_model`: the dispatched `model.ref`. `weights`: ref of a blob the adapter stored in the hub core.
- `num_examples`: training examples at the site (FedAvg weight), as reported by the client.
- `metrics`: numbers only; clients report `train_loss` and `train_acc` from `flhub_core.train`.
- `train_config_hash`: `"sha256:"` + hex sha256 of the canonical JSON of the dispatched `train_config` (without `round`): keys sorted, separators `,` and `:`, ASCII (`adapters.common.train_config_hash`).
- `client_code_version`: `flhub_core.code_version()` as reported by the client (`<version>+<sha256 prefix of the flhub_core sources>`); `"unknown"` if the framework cannot carry it.
- `created_at`: UTC, ISO 8601, seconds, `Z`.

The orchestrator rejects an envelope if: `protocol` or `round` differ; `base_model` ≠ the round's global model; `site_id` not expected for this round; `weights` blob missing; tensor names/shapes/dtypes differ from the registered model spec or values are not finite; `num_examples` ≤ 0; `train_config_hash` ≠ the round's. A `client_code_version` different from the hub's is logged as a warning.

## 4. Hub core HTTP API (`127.0.0.1:8800`)

| Method | Path | Purpose |
|---|---|---|
| PUT | `/blobs/sha256:<hex>` | store bytes; server verifies the hash (400 on mismatch); 201 created / 200 already there |
| GET, HEAD | `/blobs/sha256:<hex>` | fetch bytes / existence (404 if missing) |
| POST | `/models/spec` | register model spec: `{"spec":[{"name","shape","dtype"},…]}` in state_dict order |
| GET | `/models/spec` | fetch model spec (404 if none) |
| GET | `/health` | liveness |

Weights blobs are `safetensors` files of float32 tensors keyed by the `flhub_core` model's `state_dict` names (`flhub_core.weights.to_safetensors`; the tensor order is kept in the metadata key `order`).

## 5. Mapping per framework (what each adapter translates)

| flhub | NVFlare 2.9 | vantage6 4.15 | Flower 1.39 |
|---|---|---|---|
| dispatch(round, model) | relay `ModelController.run()` gets the job, `send_model_and_wait(targets, FLModel(params, params_type=FULL, current_round))` | `client.task.create(..., input_={"method":"train","kwargs":{weights_b64, train_config}})` to the 2 orgs | relay `ServerApp`: `Message(content=RecordDict({"arrays":ArrayRecord, "config":ConfigRecord}), message_type=TRAIN, dst_node_id=…)` via `grid.send_and_receive` |
| client config | `FLModel.meta` / job config | task kwargs | `ConfigRecord` |
| site_id | NVFlare client name | organisation name (or node name) | SuperNode `--node-config site-id=…` echoed in the reply |
| num_examples | a `num_examples` meta key | returned in the algorithm result dict | `MetricRecord["num-examples"]` |
| weights back | `FLModel.params` (FULL) | base64 safetensors in the result | `ArrayRecord` |
| client_code_version | meta key | result dict | `ConfigRecord` |
| sites online | connected clients (FLARE API `get_system_info`) | node status via vantage6 client | `grid.get_node_ids()` + site ids |

Verify the exact API names for the installed versions before coding; update this table if they differ.

**Relay pattern** (NVFlare, Flower; internal to an adapter, not part of the protocol): the framework's server-side workflow runs inside the framework server and long-polls its adapter (`GET /relay/next`, `POST /relay/result`, `/relay/progress`, `/relay/sites`, `/relay/shutdown`); weights travel as hub-core blob refs. Implemented once in `adapters/common/relay.py` (`RelayAdapter`, `RelayClient`).

## 6. Adding a new framework (BranchKey, others)

Implement the 5 methods above in a service on a free local port (simplest: subclass `adapters.common.BaseAdapter` and implement `site_status()` and `run_round()`), keep the hospital side standard for that framework, and pass `tests/conformance/`. Nothing in the orchestrator or hub core may change.

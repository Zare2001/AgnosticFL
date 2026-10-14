# flhub/0.2 — adapter protocol

**Status: flhub/0.1 FROZEN 2026-10-13; flhub/0.2 (stage 2, ADR-008) adds optional fields only (§7).** Reference implementation: `adapters/common/` (server side) and `adapters/common/rpcclient.py` (orchestrator side).

The translation layer between the framework-free orchestrator and one framework. Inspired by MCP: one small, versioned protocol; each framework gets an adapter that speaks it; the orchestrator never contains framework code. Changes need a decision record ([design/decisions.md](design/decisions.md)) and a version bump (`flhub/0.2`).

## 1. Roles

- **Orchestrator** (hub): owns rounds, FedAvg, evaluation. Calls adapters.
- **Hub core**: HTTP service on `127.0.0.1:8800` with the blob store and model registry. Used by orchestrator and adapters.
- **Adapter**: HTTP service on `127.0.0.1:881x`, method `POST /rpc`, JSON-RPC 2.0, plus `GET /health`. Wraps exactly one framework server. Stateless towards the orchestrator except for task ids. One adapter serves a fixed list of sites (its configuration).

## 2. Methods

All requests: `{"jsonrpc":"2.0","id":<int>,"method":"<name>","params":{...}}`. Every `params` carries `"protocol":"flhub/0.1"` (or `"flhub/0.2"`, §7); adapters reject unknown versions with error `-32010`. Responses always use HTTP 200 with a JSON-RPC `result` or `error`.

### `discover` → adapter description
```json
{"adapter":"nvflare","framework":"nvflare","framework_version":"2.x.y","protocol":"flhub/0.1",
 "sites":[{"site_id":"nvf-h1","status":"online"},{"site_id":"nvf-h2","status":"online"}],
 "capabilities":{"payload":"full_weights","dtypes":["float32"],"max_payload_mb":64,
                 "evaluate":false,"min_round_interval_s":0}}
```
`sites` lists every site the adapter serves. `status` ∈ `online | offline | unknown` (`unknown`: the adapter cannot tell right now, e.g. its relay is not running). An adapter is ready only when all its sites are `online` (flhub/0.2: only the sites of the run, §7; `max_payload_mb` ≥ 1024).

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

| flhub | NVFlare 2.9.0 | vantage6 4.15.2 | Flower 1.39.0 |
|---|---|---|---|
| dispatch(round, model) | relay `HubRelayController(ModelController)` long-polls the adapter and sends one task per site: `Controller.send(task, targets=[site])` with `FLModel(params, params_type=FULL, current_round=round)` | one task per site: `client.task.create(collaboration, organizations=[org_id], name="flhub-r<round>", image, input_={"method":"train","kwargs":{weights_b64, train_config, sites}}, databases=[{"label": db_label(data_variant)}])` (stage 1: one task for both organisations) | relay `ServerApp` in a long-lived run long-polls the adapter and pushes one `Message(RecordDict({"arrays": ArrayRecord, "config": ConfigRecord}), node_id, MessageType.TRAIN)` per site with `grid.push_messages`; replies arrive through `grid.pull_messages` |
| client config | `FLModel.meta["train_config"]` (the hub round is `FLModel.current_round`) | task kwargs | `ConfigRecord` `"config"` (train_config + round) |
| site_id | NVFlare client name (certificate CN; checked against the reply's `meta["client_name"]`) | organisation name; the algorithm gets its organisation id and maps it with the `sites` kwarg | SuperNode node config `site-id`, learned with a site-info QUERY message (answered by `@app.query()`) and checked in every reply |
| num_examples | `FLModel.meta["num_examples"]` (fallback `NUM_STEPS_CURRENT_ROUND`) | returned in the algorithm result dict | `MetricRecord["num-examples"]` |
| weights back | `FLModel.params` (FULL, numpy) | base64 safetensors in the result | `ArrayRecord` |
| client_code_version | `FLModel.meta["client_code_version"]` | result dict | `ConfigRecord` `"site-info"`, key `client-code-version` |
| sites online | client of the running relay job AND FLARE API `get_client_job_status()` shows it running that job (`get_system_info` keeps dead clients "connected" for minutes) | node `status` per organisation (`GET node?collaboration_id=…`) | `grid.get_node_ids()` + the site-info QUERY (the newest node per site wins) |

The cells describe the adapters as implemented for the tested versions; the framework guides ([NVFlare](frameworks/nvflare.md), [vantage6](frameworks/vantage6.md), [Flower](frameworks/flower.md)) have the details.

**Relay pattern** (NVFlare, Flower; internal to an adapter, not part of the protocol): the framework's server-side workflow runs inside the framework server and long-polls its adapter (`GET /relay/next`, `POST /relay/result`, `/relay/progress`, `/relay/sites`, `/relay/shutdown`, `GET /relay/status`; flhub/0.2 adds `POST /relay/site_result`, `POST /relay/site_error` and `GET /relay/job`, §7); weights travel as hub-core blob refs. The Flower adapter adds `POST /relay/beat`, a liveness beat of its relay run (ADR-016). Implemented once in `adapters/common/relay.py` (`RelayAdapter`, `RelayClient`).

## 6. Adding a new framework (BranchKey, others)

Implement the 5 methods above in a service on a free local port (simplest: subclass `adapters.common.BaseAdapter` and implement `site_status()` and `run_round()`), keep the hospital side standard for that framework, and pass `tests/conformance/`. Nothing in the orchestrator or hub core may change. The full guide is [Adding a framework](adding-a-framework.md).

## 7. flhub/0.2 — backward-compatible extensions (stage 2, ADR-008)

flhub/0.2 adds optional request fields and extra response fields only. A 0.2 adapter accepts `protocol` `"flhub/0.1"` and `"flhub/0.2"` and answers each caller in its own version; a 0.1 caller never sends the new fields and ignores the extra ones.

**Version negotiation.** The orchestrator calls `discover` with `"flhub/0.2"`. A 0.1 adapter answers `-32010`; the orchestrator then speaks 0.1 to it (no partial participation for that adapter).

**`discover`** echoes the request's version in `protocol` and adds:
```json
"protocols":["flhub/0.1","flhub/0.2"],
"capabilities":{..., "max_payload_mb":1024, "site_progress":true, "partial_collect":true, "site_cancel":true}
```
- **Payload cap**: `max_payload_mb` ≥ 1024. The hub core and `RpcServer` accept bodies up to 2048 MiB (`FLHUB_MAX_BODY_MB`); hub-core blobs are streamed to and from disk.
- **Readiness per run**: `sites` still lists every registered site with its status. A run needs only the sites in its own `sites` list to be online. A registered but offline site (for example an extra site of the stage-2 scaling test E5) must not block a dispatch to the others; an adapter's health check covers its base sites.

**Data variant hint.** `dispatch` takes an optional `"data_variant": "<dataset>-<pid>"` (for example `"organamnist-dir0.3"`). It repeats `train_config["data_variant"]` for adapters that must select the data inside their framework (vantage6 labelled databases); `train_config` stays opaque, and adapters that do not need the hint ignore it. Relay jobs carry it as `"data_variant"`.

**Per-site progress.** `tasks/get` adds `"sites": {site_id: "queued"|"running"|"done"|"failed"|"cancelled"}` and, when a site failed, `"site_errors": {site_id: "<framework error text>"}`. `progress.done` counts the sites that are done (or the framework's own progress, if higher).

**Partial collect.** `collect {"task_id", "partial": true}` is valid in every task state. It returns the envelopes of the sites that are done so far, in dispatch order, plus `"partial": true`, `"state"` and `"sites"`. Without `partial`, `collect` behaves as in 0.1 (only in state `completed`, else `-32013`).

**Per-site cancel.** `cancel {"task_id", "sites": [...]}` cancels those sites that are still queued or running; done and failed sites are unchanged; the other sites go on. `sites` must be a non-empty subset of the task's sites (else `-32602`). The result is `{"task_id", "state", "sites"}`. Without `sites`, `cancel` cancels the whole task as in 0.1.
- Results that arrive later for a cancelled site are dropped and logged.
- A task ends once none of its sites is queued or running: `completed` if every site is done, else `failed` if a site failed, else `cancelled`. The deadline (`deadline_s`) marks the sites still active `failed`; done sites keep their envelopes. A whole-task `cancel` ends it `cancelled`.
- `-32015` applies per site: a dispatch is busy only if one of its sites is still queued or running in another task. So after a partial collect plus a per-site cancel of the remaining sites, the next `dispatch` is accepted at once. The adapter must stop the framework work of the cancelled sites promptly, otherwise the next round of those sites waits behind it.

**Envelopes.** Unchanged, except that `protocol` echoes the version of the dispatch request. Adapters should hand over each site's envelope as soon as that site is done (`BaseAdapter.report_site()`), so that partial collect and per-site progress are meaningful.

**Relay routes (adapter-internal, §5).** Added: `POST /relay/site_result {"job_id","result":{...}}` → `{"accepted": bool}` (one site, as soon as it replied; `false` = dropped because the site was cancelled, failed or already done), `POST /relay/site_error {"job_id","site_id","error"}`, and `GET /relay/job?job_id=` → `{"job_id","state","sites"}` (404 once the adapter forgot the job). A relay should poll `/relay/job` while it waits and stop waiting for, and cancel in the framework, every site that is no longer `running`. `/relay/next` delivers only the job's sites that are still active. A final `POST /relay/result` stays allowed and is harmless after the job ended.

**Hub core (§4).** Added `DELETE /blobs/sha256:<hex>` (200, or 404 if missing) and `POST /admin/gc {"older_than_s": N}` (deletes blobs not written for N seconds) for blob GC during and between runs.

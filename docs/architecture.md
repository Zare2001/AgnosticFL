# Architecture

The hub trains one model across hospitals that use different federated-learning frameworks. It does this without asking the hospitals to change their software: per framework, an **adapter** next to the framework's server turns the hub's requests into that framework's normal operations, and every hospital trains with the same shared library, **`flhub_core`**, inside its standard client.

## Components

| Component | Code | Role |
|---|---|---|
| Orchestrator | [`hub/orchestrator/`](../hub/orchestrator/) | Owns the rounds: samples the participants, dispatches to the adapters, validates every update, aggregates, evaluates, writes a checkpoint. Contains no framework code. |
| Aggregators | [`hub/aggregators/`](../hub/aggregators/) | FedAvg and five server optimizers with float64 state ([aggregators](aggregators.md)); the same code serves the orchestrator and the simulation. |
| Hub core | [`hub/core/`](../hub/core/) | HTTP service: a blob store for model weights, addressed by sha256, and the model's tensor spec. Orchestrator, adapters and relays exchange weights through it. |
| Simulation | [`hub/sim/`](../hub/sim/) | The same round loop without any framework: every site trains in-process. The reference for the equivalence check, and the centralized and local-only baselines. |
| Adapter base | [`adapters/common/`](../adapters/common/) | The protocol ([protocol](protocol.md)) once: JSON-RPC server, task bookkeeping, envelopes, deadlines and cancels (`BaseAdapter`), and the relay pattern (`RelayAdapter`, `RelayClient`). |
| Framework adapters | [`adapters/nvflare/`](../adapters/nvflare/), [`adapters/vantage6/`](../adapters/vantage6/), [`adapters/flower/`](../adapters/flower/) | One service per framework server. NVFlare and Flower use a *relay*: a workflow inside the framework server (an NVFlare job, a Flower ServerApp) that long-polls its adapter. vantage6 needs none: its adapter is a vantage6 user that creates tasks. [`adapters/mock/`](../adapters/mock/) trains without a framework. |
| Framework servers | NVFlare server, vantage6 server, Flower SuperLink | Unchanged; they keep serving their hospitals. |
| Hospital clients | [`clients/`](../clients/) | Each framework's standard client pattern around one call to `flhub_core.train` ([client contract](client-contract.md)). |
| Shared training library | [`flhub_core/`](../flhub_core/) | Data loading and partitions, models, deterministic training and evaluation, weight serialization, the determinism selfcheck. The only code that must be identical everywhere. |

## Where things run

```
 operator's machine                                                hospitals (outbound connections only)
 ┌──────────────────────────────────────────────────────┐
 │ orchestrator ── JSON-RPC ──┬─ NVFlare adapter :8811  │   NVFlare server :8002 (mTLS) ◄── NVFlare clients
 │      │                     │    ⇅ relay job          │
 │      │                     ├─ vantage6 adapter :8812 │   vantage6 server :7601 ◄────── vantage6 nodes
 │      │                     │    (vantage6 user)      │
 │      │                     └─ Flower adapter :8813   │   Flower SuperLink :9092 ◄───── Flower SuperNodes
 │      │                          ⇅ relay ServerApp    │
 │ hub core :8800 (weights by sha256) ◄── adapters, relays
 └──────────────────────────────────────────────────────┘
```

| Port | Bound to | Used by |
|---|---|---|
| 8800 | `127.0.0.1` | hub core: orchestrator, adapters, relays |
| 8811, 8812, 8813 (8819 mock) | `127.0.0.1` | adapters: the orchestrator; relays on the same machine |
| 8002, 8003 | all interfaces (8003 closed by the firewall) | NVFlare server: hospitals (mTLS) on 8002; the adapter uses the admin port 8003 |
| 7601, 5050 | all interfaces | vantage6 server: nodes and the adapter; the image registry with the algorithm image |
| 9092, 9093 | all interfaces; `127.0.0.1` | Flower SuperLink: SuperNodes on 9092; the adapter starts relay runs through the local API on 9093 |

The ports are those of the reference testbed, which ran all of the operator's side on one machine and two simulated hospitals per framework on one machine each ([deployment](deployment.md)). The hub core and the adapters listen on `127.0.0.1`, and the relays inside the NVFlare server and the Flower SuperLink call them, so the hub, the adapters and the framework servers share one machine. Splitting them over several hosts needs other bind addresses and authentication, and has not been tested.

## One round, step by step

1. **Participants.** The orchestrator samples the round's participants from the run's sites (all of them by default; see below).
2. **Global model.** It serializes the global weights (float32 safetensors) and stores them in the hub core, which returns the blob reference `sha256:<hex>`.
3. **Dispatch.** It calls `dispatch` on every adapter that serves a participant, in parallel, with the model reference, the run's `train_config`, the adapter's sites, a deadline and, if the run uses one, the data-variant hint. Each adapter answers at once with a task id.
4. **Framework channel.** Each adapter fetches the blob and hands the round to its framework: the NVFlare relay sends one NVFlare task per site; the vantage6 adapter creates one vantage6 task per site; the Flower relay pushes one TRAIN message per SuperNode. The hospital's client receives the weights and the `train_config` plus the round number.
5. **Training.** Every hospital runs `flhub_core.train(weights, its data, config)` and returns the new weights, its number of examples and metrics, through the framework.
6. **Envelopes.** For each reply, the adapter stores the weights in the hub core and builds one **envelope**: round, site, framework, base model, weights reference, `num_examples`, metrics, the hash of the `train_config`, the client's code version. With `flhub/0.2` it hands over each site as soon as that site is done.
7. **Collect and validate.** The orchestrator polls `tasks/get` per adapter, collects the envelopes and rejects any envelope whose protocol, round, base model, site or `train_config` hash does not match, whose blob is missing, whose tensors differ from the registered spec or are not finite, whose `num_examples` is not positive, or (stage-2 configurations) whose data fingerprint does not match the partition manifest ([protocol](protocol.md) §3). With `strict_code_version`, a different `client_code_version` is rejected too.
8. **Aggregate.** The aggregator computes the new global model from the valid updates, in float64, with the sites in site-id order so that arrival order never matters, and casts once to float32.
9. **Evaluate and checkpoint.** The orchestrator evaluates the new model on its own test split and writes the round to the run directory: the global weights, the aggregator state and a record with the sha256 of both, the participants, the dropped sites, every update's metrics and timings. The record is written last, so it marks a complete round. With `retention: {blobs: rolling}` (the stage-2 setting) the round's blobs are then deleted from the hub core.

The simulation runs the same loop with every site in-process, so a real run and its simulation can be compared hash by hash after every round.

## Determinism and `flhub_core`

Aggregating across frameworks is valid only if every hospital computes its update the same way. All hospitals therefore run the same `flhub_core`, and its training is deterministic:

- one CPU thread, `torch.use_deterministic_algorithms(True)`, a fresh optimizer per call, float32 weights;
- a data order seeded by sha256(seed, round, fingerprint of the site's data), so it needs no per-hospital setting (ADR-003 in [design/decisions.md](design/decisions.md));
- every reply carries `client_code_version`, the version plus a hash of the library's source files, and `data_fp`, 32 bits of the data fingerprint.

`python -m flhub_core.selfcheck` trains each model of the model zoo for one step on synthetic data and prints one hash per model and a digest. Machines that print the same digest produce bit-identical updates, which is what made every real test run byte-identical to its simulation ([results](results/README.md)). This needs the same CPU code paths and the pinned versions of [`deploy/versions.env`](../deploy/versions.env); on mixed hardware, compare runs with a tolerance (the equivalence check accepts 1e-5) instead of hashes.

## Partial participation, deadlines and resume

Configured per run ([`hub/orchestrator/config.py`](../hub/orchestrator/config.py)):

- **Sampling.** `participation: {fraction, seed}`. The participants of round r are a pure function of (sites, fraction, seed, r), so a resumed run and the simulation draw the same sites; they are also recorded per round.
- **Strict rounds** (`min_sites: null`, the default). A round needs every participant. If a site fails or misses the deadline, the round is cancelled and retried up to `round_retries` times, after the orchestrator has waited for every participant to be online again. A retry trains from the same global model with the same seed-derived data order, so it gives the same result.
- **`min_sites: k`.** The orchestrator waits for the participants until the deadline (`deadline_s`) and accepts the round if at least k sites delivered. Sites that are still running at the deadline are cancelled per site, and their late results are dropped. With `flhub/0.2` adapters the next round can start at once.
- **Resume.** `python -m hub.orchestrator run --resume <run dir>` continues a killed run after its last complete round, with the saved configuration, global weights and aggregator state. In the test, a run whose orchestrator and adapters were killed with SIGKILL ended with weights and momentum state identical to an uninterrupted run ([stage 2](results/stage2.md#rq5-robustness-e6)).
- **Adapter and framework-server restarts.** An adapter that restarts loses its open tasks; the orchestrator treats those sites as failed and retries the round. The NVFlare relay job and the Flower relay re-attach to a restarted adapter; a restarted framework server is picked up by the adapter (results in [cost and efficiency](cost-and-efficiency.md#8-robustness-cost)).

## Trust and security model

This is a research prototype, tested on a closed testbed. Read this section before you use it with real data.

**What the hub sees.** The operator's machine sees every hospital's model update in plaintext: the relays and adapters handle the weights, the hub core stores them as blobs (deleted after each round with rolling retention), and the orchestrator aggregates them and writes the global model of every round to its run directory. Hospitals never send raw data; they send weights, the number of examples and metrics (training loss and accuracy, timings, a 32-bit data fingerprint). There is **no secure aggregation and no differential privacy**, and the validation of updates is structural (shapes, finite values, round, model, configuration, data fingerprint, optionally the code version): it does not detect a malicious or poisoned update.

**Transport, as deployed on the testbed:**

| Link | Testbed | Production needs |
|---|---|---|
| NVFlare hospitals ↔ server | mTLS from NVFlare provisioning | as tested; bind the admin port (8003) to loopback |
| vantage6 nodes ↔ server, registry | plain HTTP (server API, image registry); node API keys | HTTPS for the server and an authenticated TLS registry; pin the algorithm image by digest |
| Flower SuperNodes ↔ SuperLink | `--insecure`, no SuperNode authentication | TLS and SuperNode authentication (not tested) |
| orchestrator ↔ adapters ↔ hub core | `127.0.0.1`, no authentication | keep on loopback, or add authentication |

The testbed relied on a cloud firewall that admitted only its own machines. vantage6 ran with an unencrypted collaboration; the adapter supports only that mode (limitation in [limitations and future work](limitations-and-future-work.md)).

**Whom the hospitals trust.** One operator runs the hub and, on the testbed, all three framework servers, with the NVFlare admin kit, the vantage6 user credentials and the Flower SuperLink; whoever controls that machine controls every federation. The training code reaches the hospitals through each framework's normal channel (NVFlare job custom code, a vantage6 algorithm image, a Flower FAB), so the hospitals trust the operator's code as they would trust any job of that federation. NVFlare 2.9 kits carry no signature file; the vantage6 image is referenced by tag; the FAB is identified by its hash. Every reply reports its `client_code_version`, so the operator can verify which code each site ran (`strict_code_version: true` rejects any other).

**What a production deployment must add:** TLS and authentication on every link above; separate operators or at least separate credentials per framework; signed or digest-pinned training code; monitoring and alerting; and, where updates must stay hidden from the operator, secure aggregation or differential privacy, which this system does not provide.

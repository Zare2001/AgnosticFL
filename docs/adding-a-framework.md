# Adding a framework

A new framework needs three things: an **adapter** on the operator's side that speaks the hub's protocol, a **hospital client** in the framework's standard pattern that calls `flhub_core.train`, and deployment scripts. The orchestrator, the hub core, the aggregators and the protocol do not change; none of them changed for NVFlare, vantage6 or Flower. The **conformance suite** is the admission test.

## `BaseAdapter` or `RelayAdapter`

Both live in [`adapters/common/`](../adapters/common/) and implement the protocol ([protocol](protocol.md)) for you: parameter checks, error codes, the hub-core blobs, envelopes, deadlines, cancels and per-site progress.

| | `BaseAdapter` | `RelayAdapter` |
|---|---|---|
| Use when | an external client can start work at the sites and fetch the results through the framework's API | the framework's server-side workflow must run *inside* the framework server |
| Example | vantage6: the adapter is a vantage6 user that creates one task per site and polls its result | NVFlare: a `ModelController` in a long-running job; Flower: a ServerApp in a long-lived run |
| You write | `site_status()` and `run_round()` in the adapter | a small adapter class, plus the *relay*: a loop inside the framework server that long-polls the adapter with `RelayClient` |
| Smallest example | [`adapters/mock/service.py`](../adapters/mock/service.py), 100 code lines | [`adapters/flower/service.py`](../adapters/flower/service.py) with [`server_app.py`](../clients/flower/flhub_flower/server_app.py) |

## Methods to implement

**`BaseAdapter`** ([`adapters/common/base.py`](../adapters/common/base.py)):

```python
class MyAdapter(BaseAdapter):
    adapter_name = "myfw"
    framework = "myfw"
    framework_version = "1.2.3"

    def site_status(self) -> dict[str, str]:
        """{site_id: "online" | "offline" | "unknown"} for every site this adapter serves."""

    def run_round(self, task, weights, client_config) -> list[SiteResult]:
        """Blocking, in a worker thread. Send `weights` and `client_config` (the dispatch train_config
        plus "round") to `task.sites` through the framework; report each site as soon as it is done."""
        ...
        self.report_site(task, SiteResult(site, num_examples, metrics, client_code_version, weights=new))
        self.report_site_error(task, other_site, "<the framework's own error text>")
        return []
```

`run_round` should poll `task.cancelled`, `task.site_cancelled(site)` and `task.deadline_exceeded()` while it waits, and stop the framework's work for a cancelled site; `on_cancel_sites(task, sites)` is called right after a per-site cancel. Start the service with `MyAdapter(sites, HubClient(hub_url)).serve(host, port)`.

**`RelayAdapter`** ([`adapters/common/relay.py`](../adapters/common/relay.py)): the adapter class needs little more than its name (optionally its own `site_status()`). The relay runs inside the framework server:

```python
relay = RelayClient("http://127.0.0.1:<adapter port>")
while True:
    job = relay.next_job(timeout=30)              # {"type": "train" | "idle" | "shutdown", ...}
    if job["type"] == "shutdown":
        break
    if job["type"] != "train":
        continue
    weights = relay.get_model(job)                # {name: float32 ndarray} from the hub core
    # send weights + job["config"] to job["sites"] through the framework; for each reply:
    relay.post_site_result(job["job_id"], relay.result(site, new_weights, n, metrics, code_version))
    # or: relay.post_site_error(job["job_id"], site, "<the framework's own error text>")
    # while waiting: relay.job_status(job["job_id"])["sites"]; stop and cancel every site not "running"
```

`job["config"]` is the dispatch `train_config` plus the round; pass it unchanged to the hospitals.

## `flhub/0.2` features

| Feature | What the adapter must do | Built on |
|---|---|---|
| per-site progress and partial collect | report each site when it is done (`report_site`, `post_site_result`) | `BaseAdapter`, relay routes |
| per-site failure | report a failed site with the framework's error text; the other sites go on | `report_site_error`, `post_site_error` |
| per-site cancel | stop the framework's work for a cancelled site promptly, or its next round waits behind it | `task.site_cancelled`, `on_cancel_sites`, `job_status` |
| per-run readiness | list registered but offline sites as `offline`; they must not block a dispatch to the others | `site_status()` |
| data variants | if the framework, not the client, selects the data, map the dispatch hint `data_variant` (vantage6: a database label) | `task` / job field `data_variant` |
| large payloads | nothing: blobs are streamed; the adapter base accepts bodies up to 2048 MiB | hub core, `RpcServer` |

An adapter that reports only at the end (the `flhub/0.1` style) still works, but `min_sites` and deadlines then cannot use its partial results.

## The hospital side

Write the client in the framework's standard pattern around one call to `flhub_core.train`, and ship it through the framework's own code channel (job, image, app bundle): see the [client contract](client-contract.md) and the three example clients in [`clients/`](../clients/). The only hub-specific items at a hospital should be the connection to the framework server, its credentials and the data location.

## The conformance suite

[`tests/conformance/`](../tests/conformance/) runs 13 tests against a running adapter:

| Test | Checks |
|---|---|
| health, discover schema, all sites online | the service answers; `discover` is complete and well-formed |
| error codes | unsupported protocol (`-32010`), unknown method, unknown site (`-32011`), unknown task (`-32012`) |
| round matches direct training | every returned update equals `flhub_core.train` on the same data, to 1e-6 (0.0 in every test of the three adapters) |
| busy, then a second round | a second dispatch for busy sites is `-32015`; later rounds work |
| cancel, then recover | `cancel` works and the next round is correct |
| subset dispatch | a dispatch to one site trains only that site |
| partial collect + site cancel + next dispatch | cancel one running site, collect the others, dispatch the next round at once |
| offline site does not block | a registered but offline site does not block the others |
| adapter restart between rounds | the adapter comes back and serves the next round (`--restart-cmd`) |
| payload-64 echo | 64 MiB per site each way, returned exactly |
| data-variant round | one round on a stage-2 data variant (`--variant`) |

```bash
python -m pytest tests/conformance --adapter http://127.0.0.1:<port> --hub http://127.0.0.1:8800 \
    --sites <site ids> --restart-cmd "<command that restarts the adapter>" \
    --variant organamnist-dir0.3 --report conformance-<fw>.json -v -s
```

The suite compares every update with local training on the benchmark partitions, so the sites under test must be the benchmark sites (`nvf-h1`, `v6-h3`, `flw-h5`, …) with the data written by `python -m flhub_core.data materialize`: run it on test clients provisioned with those names, not on real hospitals. `python -m pytest tests/conformance --adapter mock` is the self-test of the suite itself.

## Deployment scripts and configuration

- **Operator side:** `deploy/central/<fw>/{install,start,stop,status}.sh`, where `status.sh` exits 0 only if the framework server and the adapter are up and every base site is online (the convention of the three existing frameworks, [`deploy/`](../deploy/README.md)).
- **Hospital side:** `deploy/<fw>-vm/{install,start,stop,status}.sh` for the test hospitals.
- **Run configuration:** add the adapter and its sites to [`configs/stage2/topology.yaml`](../configs/stage2/topology.yaml) (or to the `adapters:` list of a stage-1-style configuration) with a free local port.

## Checklist

- [ ] Adapter: `BaseAdapter` or `RelayAdapter` subclass; `site_status()`; per-site reporting, errors and cancels; the framework's own error text in failures.
- [ ] Relay (if any): long-polls the adapter, sends the configuration unchanged, posts each site's result as it arrives, honours cancels, survives an adapter restart.
- [ ] Hospital client: the framework's standard pattern, one call to `flhub_core.train`, returns weights, `num_examples`, metrics and `flhub_core.code_version()` ([client contract](client-contract.md)).
- [ ] Rollout: new `flhub_core` versions reach the hospitals through the framework's code channel, with no hospital step.
- [ ] `python -m flhub_core.selfcheck` at a test hospital prints the hub's digest.
- [ ] Conformance suite: 13 of 13 against a test deployment.
- [ ] A few rounds with the orchestrator, then `python -m hub.sim --config <config> --compare <run dir>`: `hashes_equal=True`.
- [ ] Deployment scripts with a `status.sh`; a topology entry; a guide in `docs/frameworks/<fw>.md` from [the template](frameworks/_template.md).

## Implementation effort of the existing adapters

Code lines are non-blank, non-comment lines ([`scripts/docs/loc.py`](../scripts/docs/loc.py), [loc.md](results/evidence/loc.md)); all three build on the shared adapter base (1,182 lines).

| | NVFlare | vantage6 | Flower |
|---|---|---|---|
| Adapter type | `RelayAdapter` | `BaseAdapter` | `RelayAdapter` |
| Adapter code lines | 604 in 5 files: service 239, relay controller 208, FLARE API wrapper 84, job export 72, package 1 | 290: service 288, `__main__` 2 | 553: adapter and relay launcher 210, relay ServerApp 342, package 1 |
| Hospital client | 39 | 38 algorithm + 16 Dockerfile, 11 configuration lines | 75 (ClientApp 50, records 24, package 1), 26 configuration lines |
| Unit tests | 175 lines, 12 tests (fake FLARE API) | 225 lines, 10 tests (fake vantage6 client) | 275 lines, 11 tests (fake `Grid` running the real ClientApp) |
| Testbed deployment, incl. experiment tools | 1,137 | 1,162 (+ 107 configuration) | 940 (+ 27 configuration) |
| Framework concepts used | 6: server workflow (`ModelController`), `FLModel`, Client API, Job API, FLARE API, provisioning | 10: user, organisation, collaboration, node, task, run, result, database label, algorithm image, node policy (14 distinct client calls) | ServerApp and `Grid` messaging, records (`ArrayRecord`, `ConfigRecord`, `MetricRecord`), ClientApp, `flwr run`, SuperLink and SuperNode options |

Sources: the framework guides' §5.2 ([NVFlare](frameworks/nvflare.md), [vantage6](frameworks/vantage6.md), [Flower](frameworks/flower.md)). Moving the NVFlare adapter from `flhub/0.1` to `flhub/0.2` changed it by +313/−94 lines and its hospital client by 0 lines. More on what each framework costs to run: [cost and efficiency](cost-and-efficiency.md).

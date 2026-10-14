# Limitations and future work

What the test results do and do not show, what is known to be wrong or incomplete, and what would come next.

## Testbed limits

- **Hardware.** Four VMs with 4 vCPUs (AMD EPYC-Rome), 31 GiB RAM and 19 GB disk, CPU-only. The disk stopped the payload sweep: 1024 MiB updates for one framework and 256 MiB for the three-framework federation did not fit ([stage 2](results/stage2.md#e4a-payload-sweep)). CPU-only training limited ResNet-18 to 20 rounds, too few to converge (42.7 % with FedAvg).
- **Network.** All machines in one data centre (about 0.7 ms round-trip time). Wide-area links exist only as `netem` emulation of rate and delay, without loss, jitter or competing traffic.
- **Simulated hospitals.** Two hospitals per framework share one VM, so co-located sites compete for CPU, memory, disk and the link; round times include that contention. No real hospital network, firewall or data took part.
- **One operator machine.** The hub and all three framework servers ran on one VM. Its loss would stop every federation; the tests covered restarts, not the loss of the machine.

## Method limits

- **One seed on the real system.** Every real run used seed 2026; seeds 2027 and 2028 of the learning experiments ran in the simulation, which is valid only because each real run is hash-identical to its simulation.
- **Tuning.** Server learning rates were tuned once, on one label-skewed partition, the small CNN and full participation, with one seed; four optima sit on the edge of the grid. The tuned FedAvgM step diverged with a larger model and with partial participation ([aggregators](aggregators.md#divergence-findings)).
- **One dataset family.** MedMNIST at 28 × 28 pixels (PneumoniaMNIST, OrganAMNIST); the 64-pixel variant was not run.
- **Uniform hardware.** Bit-exact equality between hospitals, hub and simulation depends on identical CPUs and pinned versions; on mixed hardware the equivalence check needs its tolerance (1e-5).
- **Timing granularity.** Per-site times come from polling (0.25 s in the orchestrator, about 1 s or more in the frameworks), so sub-second differences are not resolved.

## Framework findings that need upstream work

Each finding is described with its cause in the framework guide's §6, and drafts of upstream issues (not filed) are in its §8.

**NVFlare 2.9.0** ([guide](frameworks/nvflare.md#6-limits-known-issues-and-knobs)):

1. One client's job-process failure fails the whole job for all clients (no setting).
2. No per-task abort towards a client: a cancelled task keeps training at the hospital.
3. A restarted client cannot rejoin a running job; the adapter restarts its relay job between rounds.
4. After an abrupt server stop, jobs that were running stay `RUNNING` in the job store.
5. A job's `custom/` folder is appended to `sys.path`, so an installed package shadows the job's copy.

**vantage6 4.15.2** ([guide](frameworks/vantage6.md#6-limits-known-issues-and-knobs)):

1. A node drops a finished result when its websocket is down, and trains again after reconnecting.
2. The server rewrites the whole run row (including its input) for every algorithm log line; with SQLite this blocks the single worker for minutes with large models.
3. Task listings load every stored input and result; deleting a task with subtasks orphans their runs.
4. Nodes never delete their task folders.
5. The large-result store is not usable (1 MiB upload chunks against uWSGI's 1 MiB limit).
6. The command-line tool has formatting bugs on Python 3.11 (worked around, ADR-005 in [design/decisions.md](design/decisions.md)).

**Flower 1.39.0** ([guide](frameworks/flower.md#6-limits-known-issues-and-knobs)):

1. A running ServerApp ends with the SuperLink, and Flower does not restart the run.
2. The SuperLink's SQLite database keeps its peak size until it is vacuumed.
3. A SuperNode exits when a SuperLink without a database forgets its id; offline detection is heartbeat-based (up to 60 s).

## Known inconsistencies

| Item | Status |
|---|---|
| [`clients/flower/pyproject.toml`](../clients/flower/pyproject.toml) declares `license = "Apache-2.0"` as Flower app metadata, while no license has been chosen for this repository ([README](../README.md#license)) | kept as is until the author chooses a license |
| [`scripts/equivalence_check.py`](../scripts/equivalence_check.py) stops on run directories written by the current orchestrator: its file pattern also matches the aggregator-state files `rNNNN.state.safetensors` | use `python -m hub.sim --compare <run dir>`, which compares the recorded hashes; fixing the script is a code change |
| The vantage6 adapter supports only unencrypted collaborations (it calls `setup_encryption(None)`) | needs an adapter change |
| The figure `e4a_peak_rss_vs_payload.png` labels its axis MB; the stage-2 report at its tag writes GB for values in MiB / 1000 | the documentation uses MiB and GiB; the values are MiB |
| The generated E3 resource table ([tables.md](results/evidence/stage2/tables.md)) lists vantage6's "largest process" as one container; it is the sum of both sites' training containers (2.1 GiB) | stated correctly in the documentation |
| Docstrings and comments in the code and scripts cite the documents of the build: `PLAN.md`, `EXPERIMENTS.md`, `PROJECT_PLAN.md`, `REPORT_TEMPLATE.md`, `COLLABORATION.md`, `PROTOCOL.md` and `docs/DECISIONS.md` | the planning documents are on branch `archive/agentic-build`; `PROTOCOL.md` is now [docs/protocol.md](protocol.md) and `docs/DECISIONS.md` is [docs/design/decisions.md](design/decisions.md); the code was not changed in the restructure |

## Future work

In order of priority:

1. **Privacy and security.** Secure aggregation between the adapters, so that the hub sees only the aggregate (a protocol extension with masked updates and an aggregation step), and differential privacy at the clients through `flhub_core`. TLS on every link: Flower TLS with SuperNode authentication, HTTPS for vantage6, an authenticated registry and images pinned by digest; the NVFlare admin port on loopback; authentication for the hub's own services; separate operators per framework.
2. **vantage6 at larger models.** A PostgreSQL server database or `share_algorithm_logs: false` at the nodes (both candidates are untested), support for encrypted collaborations in the adapter, and a test with vantage6 5.x.
3. **Framework isolation and latency.** One NVFlare relay job per hospital, so that one hospital's failure no longer touches the others; a lower NVFlare `task_request_interval` for small models; a Flower relay that survives a SuperLink restart and a SuperLink database that releases space.
4. **Methods.** More seeds on the real system; a wider tuning grid, with server learning rates per model size and participation level; longer runs of larger models; federated evaluation at the sites; robust aggregators; client control variates (SCAFFOLD).
5. **Infrastructure.** Heterogeneous hardware with the tolerance mode, real wide-area links, the hub on a separate host from the framework servers, and a pilot with real hospitals, data-quality checks and a governance model for the hub operator.
6. **More frameworks** through the same adapter contract ([adding a framework](adding-a-framework.md)).
7. **Packaging.** Published packages for `flhub_core` and the hub, continuous integration with the unit tests and the mock conformance suite, and a license.

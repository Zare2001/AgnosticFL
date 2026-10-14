# Deployment

How to run the hub next to framework servers you already have, and how the reference testbed of the test stages was deployed. The per-framework steps are in the guides' §4: [NVFlare](frameworks/nvflare.md#4-deploying-next-to-an-existing-nvflare-federation), [vantage6](frameworks/vantage6.md#4-deploying-next-to-an-existing-vantage6-federation), [Flower](frameworks/flower.md#4-deploying-next-to-an-existing-flower-federation).

## Next to an existing framework server

The hospitals keep their framework, client and credentials. Everything below happens on the operator's side, except the small hospital-side items listed per framework.

1. **Host.** Run the hub core, the orchestrator and the adapters on the machine of the framework servers (tested), or at least on a host that reaches each server's API and is reachable by the relays: the hub core and the adapters listen on `127.0.0.1`, and the NVFlare relay job and the Flower relay ServerApp call them from inside the framework servers ([architecture](architecture.md#where-things-run)).
2. **Software.** A Python 3.11 environment with the hub package and `flhub_core` from a clone of this repository, `uv pip install -e ./flhub_core -e .`, plus torch, numpy and safetensors at the versions of [`deploy/versions.env`](../deploy/versions.env) (the commands are in [getting started](getting-started.md#install)). The NVFlare relay runs inside the NVFlare server's Python environment and the Flower relay inside a Flower run, so those environments need the packages too ([NVFlare](frameworks/nvflare.md) §4, [Flower](frameworks/flower.md) §4.2).
3. **Hub core.** `python -m hub.core --port 8800 --root <directory for blobs>`; on the testbed it runs as a systemd unit ([`deploy/central/install.sh`](../deploy/central/install.sh)).
4. **Adapters**, one per framework:

   | Framework | What the adapter needs | Hospital-side items |
   |---|---|---|
   | NVFlare 2.9 | an admin startup kit allowed to submit jobs with custom code; the relay job exported with `python -m adapters.nvflare.job` | remove an installed `flhub_core` and restart the client once; the data file |
   | vantage6 4.15 | a vantage6 user in an organisation that takes part in an unencrypted collaboration; the algorithm image in a registry the nodes can reach | allow the image in the node policy; one database per data variant; restart the node once |
   | Flower 1.39 | access to the SuperLink's HTTP API (`flwr run`) through a connection in `$FLWR_HOME/config.toml` | `site-id` (and optionally `data-path`) in the SuperNode's node config; restart it once; the data file |

5. **Configuration.** List each adapter's URL and sites in a topology ([`configs/stage2/topology.yaml`](../configs/stage2/topology.yaml)) and write run configurations that name the sites ([`configs/`](../configs/README.md)).
6. **Check.** Every adapter's `discover` lists its sites as `online`. Run the conformance suite against test clients provisioned with the benchmark site ids ([adding a framework](adding-a-framework.md#the-conformance-suite)), then a few rounds with the real hospitals: every site's `client_code_version` is in the round records (`rounds/rNNNN.json`), and `strict_code_version: true` rejects updates from other code.

## Ports

| Port | Service | Reachable from |
|---|---|---|
| 8800 | hub core | the operator's machine only |
| 8811, 8812, 8813 | NVFlare, vantage6 and Flower adapters | the operator's machine only |
| 8002 | NVFlare server (federated learning, mTLS) | NVFlare hospitals |
| 8003 | NVFlare admin (FLARE API) | the adapter; keep closed to the outside |
| 7601 | vantage6 server API | vantage6 hospitals and the adapter |
| 5050 | image registry (vantage6 algorithm image) | vantage6 hospitals |
| 9092 | Flower SuperLink Fleet API | Flower hospitals |
| 9093 | Flower SuperLink HTTP API (`flwr run`) | the adapter only (`127.0.0.1`) |

These are the testbed's values ([`deploy/topology.env`](../deploy/topology.env)); hospitals open only outgoing connections.

## Credentials

| Framework | The adapter holds | The hospitals hold | Hub-specific for hospitals |
|---|---|---|---|
| NVFlare | the operator's admin kit (controls the NVFlare project) | their startup kits (mTLS certificates) from `nvflare provision` | none |
| vantage6 | a user password (controls what that user's organisation may do) | their node API keys from the server | none |
| Flower | none on the testbed (`--insecure`) | none on the testbed | none |

The hub adds no credentials at the hospitals. Keep the operator's credentials in files readable only by the operator (the testbed uses `~/flhub-runtime/` and `~/.flhub/secrets.env`, mode 600, never the repository). What a production deployment must add (TLS, authentication, image pinning) is in the [trust and security model](architecture.md#trust-and-security-model).

## Health checks

- `GET /health` on the hub core and on every adapter answers `{"ok": true, ...}` when the service is up.
- `discover` (JSON-RPC) lists every site of an adapter with `online`, `offline` or `unknown`; a run needs only its own sites online.
- The relays report their liveness at `GET /relay/status` on their adapter.
- On the testbed, `bash deploy/central/status.sh` checks all of this and exits 0 only if every service is up and every base site online (`HEALTHY`); each hospital machine has its own `status.sh`.

## The reference testbed

Four VMs (4 vCPU AMD EPYC-Rome, 31 GiB RAM, 19 GB disk, Ubuntu 24.04) in one data centre:

| Machine | Runs |
|---|---|
| central | hub core, orchestrator, NVFlare server + relay job + adapter, vantage6 server + adapter + image registry, Flower SuperLink + relay ServerApp + adapter |
| NVFlare hospitals | `nvf-h1`, `nvf-h2` (and `nvf-h7`, `nvf-h8` for the 12-site test): NVFlare clients |
| vantage6 hospitals | `v6-h3`, `v6-h4` (and `v6-h9`, `v6-h10`): vantage6 nodes in Docker |
| Flower hospitals | `flw-h5`, `flw-h6` (and `flw-h11`, `flw-h12`): Flower SuperNodes under a restart supervisor |

A cloud firewall admitted only the four machines on the ports above. Watchdog cron jobs restart hospital clients that stop.

## What the deploy scripts do

[`deploy/`](../deploy/README.md) has `install.sh`, `start.sh`, `stop.sh` and `status.sh` per machine and framework; the order of a full deployment is in [`deploy/README.md`](../deploy/README.md), and each guide lists its scripts in detail ([NVFlare](frameworks/nvflare.md) §2.5, [vantage6](frameworks/vantage6.md) §2.5 and §4, [Flower](frameworks/flower.md) §2.7). Purge scripts keep the framework stores from filling the disk between large runs ([cost and efficiency](cost-and-efficiency.md#62-disk)). The other files in the hospital folders are experiment tooling (fault injection, CPU throttling, link shaping, monitoring) and are not needed to run the hub.

# deploy

Install, start, stop and status scripts of the reference testbed: one central machine (hub core, orchestrator, the three framework servers and their adapters) and one hospital machine per framework with two simulated hospitals each. How to deploy the hub next to a framework server you already run is in [deployment](../docs/deployment.md); the per-framework details are in the guides ([NVFlare](../docs/frameworks/nvflare.md), [vantage6](../docs/frameworks/vantage6.md), [Flower](../docs/frameworks/flower.md)).

| Path | What |
|---|---|
| `versions.env` | pinned versions for every machine and image (Python, torch, numpy, frameworks) |
| `topology.env` | hosts and ports of the reference testbed (a few other scripts here and in `scripts/stage2/` also name its hosts) |
| `central/` | `install.sh` (hub venv, dataset, Docker registry, hub-core systemd unit), `start.sh`, `stop.sh`, `status.sh` (exit 0 only when every service is up and every base site online) |
| `central/nvflare/`, `central/vantage6/`, `central/flower/` | the framework server and its adapter on the central machine: `install.sh`, `start.sh`, `stop.sh`, `status.sh`, restart and purge scripts, provisioning (NVFlare), site registration (vantage6), and the native FedAvg baselines (`native/`) |
| `nvflare-vm/`, `vantage6-vm/`, `flower-vm/` | the hospital machines: `install.sh`, `start.sh`, `stop.sh`, `status.sh`, the extra sites of the 12-site test (`extra.sh`), and experiment tooling (fault injection, CPU throttling, link shaping, monitoring, end-state checks) |

**Order of a full deployment:**

1. Central machine: `bash deploy/central/install.sh && bash deploy/central/start.sh`, then per framework `bash deploy/central/<fw>/install.sh && bash deploy/central/<fw>/start.sh`.
2. Hand the hospital credentials to the hospital machines over a secure channel: NVFlare startup kits, vantage6 node API keys (Flower on the testbed needs none). On the testbed this went through the message bus in [`tools/`](../tools/README.md) with client-side encryption.
3. Hospital machines: `bash deploy/<fw>-vm/install.sh && bash deploy/<fw>-vm/start.sh`.
4. Check: `bash deploy/central/status.sh` on the central machine (`HEALTHY`) and `bash deploy/<fw>-vm/status.sh` on each hospital machine.

The scripts assume Ubuntu 24.04, `uv`, Docker (central and vantage6 machines), the paths in `topology.env` and run-time state under `~/flhub-runtime/` (never inside the repository). Credentials live in the run-time directories and in `~/.flhub/secrets.env`, never in git.

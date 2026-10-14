# NVFlare adapter

Server-side adapter between the hub and an NVFlare 2.9 server. It keeps one long-running NVFlare job (`flhub-relay`) alive and relays every hub round to the NVFlare clients as one task per site.

| File | Role |
|---|---|
| `service.py` | adapter service on `127.0.0.1:8811` (flhub JSON-RPC and relay routes), relay-job keeper, site watcher |
| `relay_controller.py` | `HubRelayController`, the `ModelController` workflow that runs inside the NVFlare server job |
| `job.py` | exports the relay job folder with the Job API: `python -m adapters.nvflare.job --out <dir>` |
| `flare_admin.py` | FLARE API session with the operator's admin startup kit |
| `native.py` | NVFlare's built-in `FedAvg` with recording hooks, used only for the native-vs-hub comparison |

Start it with `python -m adapters.nvflare.service --admin-kit <admin kit dir> --job-dir <dir>/flhub-relay --sites <client names>`; the reference testbed uses `deploy/central/nvflare/{install,start,status,stop}.sh`. Full guide: [docs/frameworks/nvflare.md](../../docs/frameworks/nvflare.md).

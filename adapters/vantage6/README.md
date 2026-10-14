# vantage6 adapter

The flhub/0.2 adapter for a vantage6 4.15 server. It logs in as an ordinary vantage6 user and runs every hub round as one vantage6 task per site, using the algorithm image built from [`clients/vantage6`](../../clients/vantage6/).

| File | What |
|---|---|
| `service.py` | the adapter (`Vantage6Adapter`, a `BaseAdapter` subclass) and its command line |
| `__main__.py` | entry point for `python -m adapters.vantage6` |
| `native.py` | tooling for the E7 baseline (vantage6's own FedAvg); not needed to run the adapter |

Start it next to the vantage6 server, with the password of its vantage6 user in `V6_ADAPTER_PASSWORD`:

    python -m adapters.vantage6 --port 8812 --sites v6-h3 v6-h4 --image <registry-host>:5050/flhub/v6-train:<tag>

The reference deployment installs and starts it with `deploy/central/vantage6/install.sh` and `start.sh`. Mapping, configuration and limits: [the vantage6 guide](../../docs/frameworks/vantage6.md).

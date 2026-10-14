# Flower app (FAB)

The Flower app that the hub ships to Flower hospitals as a FAB (Flower App Bundle). `flhub_flower/client_app.py` is the hospital-side code: the standard `ClientApp`, with `@app.train()` around one call to `flhub_core.train` and `@app.query()` for the site id; `flhub_flower/records.py` converts weights to and from an `ArrayRecord`. `flhub_flower/server_app.py` is the adapter's relay `ServerApp`: it travels in the same FAB but runs only next to the SuperLink.

Hospitals do not install it. `adapters/flower/service.py` adds `flhub_core` to the app and submits it with `flwr run`; every SuperNode fetches the FAB with the run, so a new version needs no hospital step and no restart.

A hospital needs only:

- the standard `flower-supernode` with `--superlink <central-host>:9092` and `--node-config "site-id='<site>' data-path='<file>'"`;
- a Python environment with `flwr`, `torch`, `numpy` and `safetensors` (`flhub_core` comes with the FAB and takes precedence over an installed copy);
- its data file (`data-path`, else `$FLHUB_DATA_DIR/<site>.csv`).

What each line does, and the checklist for your own Flower client: [docs/frameworks/flower.md §3](../../docs/frameworks/flower.md#3-client-side-what-the-hospital-code-must-contain).

# NVFlare hospital client

`client.py` is the hospital-side code for NVFlare: the standard Client API loop (`flare.init`, `flare.receive`, `flare.send`) around one call to `flhub_core.train`.

Hospitals do not install it. `adapters/nvflare/job.py` packs it into the relay job as custom code (`custom/client.py`, next to `custom/flhub_core/`), and NVFlare runs it in-process with `ScriptRunner` (numpy, `FULL` weights).

A hospital needs only:

- its NVFlare startup kit (server address and mTLS certificates);
- a Python environment with `nvflare`, `torch`, `numpy` and `safetensors`, and **no** `flhub_core` (the job ships it);
- its data file: `$FLHUB_DATA_PATH`, else `$FLHUB_DATA_DIR/<site>.csv`.

What each line does, and the checklist for your own NVFlare client: [docs/frameworks/nvflare.md §3](../../docs/frameworks/nvflare.md#3-client-side-what-the-hospital-code-must-contain).

# Adding a framework: skeleton adapter and client stub

A runnable starting point for a fourth framework. It has both adapter patterns and the hospital-side round. A toy framework (`toy_framework.py`) stands in for your framework, so the files run on one machine as they are. Both skeletons pass the [conformance suite](../../docs/adding-a-framework.md#the-conformance-suite), and the unchanged orchestrator runs rounds through them that are equal to the framework-free simulation. The guide to the steps around it is [Adding a framework](../../docs/adding-a-framework.md).

| File | What it is | What you change |
|---|---|---|
| [`adapter.py`](adapter.py) | direct pattern: a `BaseAdapter` with the two hooks `site_status()` and `run_round()` and the optional cancel hooks | the lines marked `REPLACE`: calls to your framework's server API |
| [`relay.py`](relay.py) | relay pattern: a `RelayAdapter` (no hooks) and the relay loop that runs inside the framework's server | the `REPLACE` lines in the relay loop: your framework's messages to the sites |
| [`client.py`](client.py) | the hospital-side round of the [client contract](../../docs/client-contract.md) | nothing; call `train_round()` from your framework's client pattern |
| [`toy_framework.py`](toy_framework.py) | stand-in for a framework: connected sites, submit, poll, cancel; its hospitals run `client.py` | delete it; it is your framework |
| [`run.yaml`](run.yaml) | a run configuration that registers the adapter with the orchestrator | the adapter's name, URL and sites |
| [`check.sh`](check.sh) | conformance suite against both skeletons, and three orchestrator rounds with the equivalence check | the commands that start your adapter |

## Client: what the hospital code does

[`client.py`](client.py) is the flhub part of a hospital client, the body that every framework's client pattern wraps:

- **Receive** the global weights (an ordered `{tensor name: float32 array}`) and the hub's `train_config` with `round` (and `data_variant` when the hub sets one), unchanged.
- **Run** `flhub_core.train(weights, data, config)` on the site's own data: `load_data()` opens `$FLHUB_DATA_PATH`, or `<$FLHUB_DATA_DIR>/<site>.csv`, as a lazy handle, and `train()` selects the partition the `data_variant` names.
- **Return** the new weights (same names, shapes, dtype), `num_examples`, the metrics (including `data_fp`) and `flhub_core.code_version()`.

The wrapper around it is your framework's standard client pattern: it receives the framework's message, calls `train_round()` and puts the result into the framework's reply. The three tested wrappers are the NVFlare Client API loop ([example](../nvflare/)), a Flower `ClientApp` ([example](../flower/)) and a vantage6 algorithm function ([example](../vantage6/)).

## Server: the adapter

Choose the pattern by where the round can run ([Adding a framework](../../docs/adding-a-framework.md#baseadapter-or-relayadapter)):

- **Direct pattern** ([`adapter.py`](adapter.py)), when an outside process can send work to the sites through the framework's API, as for vantage6. `BaseAdapter` implements the protocol methods, parameter checks, busy and error codes, deadlines, cancels, the hub-core blobs and the envelopes. You write `site_status()` (which sites are connected) and `run_round()`, which runs in a worker thread: send the weights and the config to each site, then report each site as soon as it is done with `report_site()`, or `report_site_error()` when it failed. Stop waiting for a site once `task.site_cancelled(site)`, and stop the round once `task.terminal`. `on_cancel()` and `on_cancel_sites()` stop the framework's work.
- **Relay pattern** ([`relay.py`](relay.py)), when only code inside the framework's server can reach the sites, as for NVFlare and Flower. The adapter service is a `RelayAdapter` subclass without hooks. The relay, inside the framework's server, loops: report the connected sites (`report_sites`), long-poll for a job (`next_job`), fetch the model from the hub core (`get_model`), send it to the sites, watch the adapter's view of the job for cancels (`job_status`), and post each site's reply (`post_site_result` with `result()`, which stores the weights as a hub-core blob, or `post_site_error`).

Both adapters serve the protocol on one port. The orchestrator finds an adapter only through an entry in the run configuration ([`run.yaml`](run.yaml)): a name, the URL and the sites. The hub core is the blob store for the weights; the adapter gets its URL (`--hub`).

## Run and check

In the virtual environment of [getting started](../../docs/getting-started.md), from the repository root:

```bash
export FLHUB_DATA_DIR=/tmp/agnosticfl/data
bash examples/new-framework/check.sh
```

The script starts a temporary hub core, then each skeleton (the relay pattern as two processes, adapter and relay), on free local ports in a temporary directory, and removes everything at the end, also when it is interrupted (Ctrl-C, `timeout`, `kill`, a closed terminal). It runs `pytest tests/conformance` against each skeleton, with the sites `nvf-h1` and `nvf-h2` (benchmark sites, whose partitions the suite trains directly as the reference), a registered site `toy-h7` that is not connected, and a restart command for the adapter. Between the two, the orchestrator runs [`run.yaml`](run.yaml) (three rounds) through the direct skeleton and `hub.sim --compare` checks the run against the framework-free simulation. With `FLHUB_VARIANT=organamnist-dir0.3` the script first builds that partition (downloading OrganAMNIST once) and also runs the data-variant test.

By hand, with the ports of [`run.yaml`](run.yaml):

```bash
python -m hub.core --port 8800 --root /tmp/agnosticfl/hubcore &
python examples/new-framework/adapter.py --port 8820 --hub http://127.0.0.1:8800 &
python -m pytest tests/conformance --adapter http://127.0.0.1:8820 --sites nvf-h1 nvf-h2 -v
python -m hub.orchestrator run --config examples/new-framework/run.yaml --runs-dir /tmp/agnosticfl/runs
```

## Tested

A fresh clone of `fix/examples-check-cleanup` at `d4065ad` in a fresh virtual environment, installed as in [getting started](../../docs/getting-started.md) (Python 3.11.17, torch 2.14.1+cpu, numpy 2.4.4, `flhub_core` 0.2.0+fe357a1dbec1), Ubuntu 24.04 on AMD EPYC-Rome, 2026-10-14:

| Command | Result |
|---|---|
| `bash examples/new-framework/check.sh` | direct skeleton: 12 passed, 1 skipped (the data-variant test); orchestrator `"status": "finished"`, `"final/test_acc": 0.625` after three rounds; `INV-1 full sim vs …/toy-3r-latest: hashes_equal=True max_abs_diff=0.000e+00 -> PASS`; relay skeleton: 12 passed, 1 skipped; 44 s including the first PneumoniaMNIST download; afterwards no process of the run is left |
| `FLHUB_VARIANT=organamnist-dir0.3 bash examples/new-framework/check.sh` | both skeletons 13 passed; orchestrator and INV-1 as above; 50 s with OrganAMNIST already downloaded (the download, 38 MB, took most of 11 min in another run); no process left |
| The script interrupted 12 s after its start, during the direct skeleton's conformance run: `timeout 12`, SIGHUP to the script, Ctrl-C (SIGINT to its process group), SIGTERM to its process group, SIGTERM to the script (`kill`) | exit 124, 129, 130, 143 and 143 (in this order); the script ends at once, after Ctrl-C within 1.2 s; 5, 30 and 90 s later no process of the run and no temporary directory is left |
| `python -m pytest`; `python -m pytest tests/conformance --adapter mock` | 79 passed, 16 skipped; 12 passed, 1 skipped, as in getting started (the examples add no tests to the default run) |

## From the skeleton to an adapter

1. Copy `adapter.py` or `relay.py` to `adapters/<framework>/`, replace the `REPLACE` lines with your framework's calls and read connection settings and credentials from your configuration, never from the code.
2. Wrap `train_round()` in your framework's client pattern at the hospitals; ship `flhub_core` with it through the framework's own channel ([client contract](../../docs/client-contract.md#how-new-library-versions-reach-the-hospitals)).
3. Run the conformance suite against a real deployment with two benchmark sites, then a run configuration with the new adapter, and `hub.sim --compare` on the run.
4. Follow the [checklist](../../docs/adding-a-framework.md#checklist): deployment scripts with a status check, a framework guide from the [template](../../docs/frameworks/_template.md).

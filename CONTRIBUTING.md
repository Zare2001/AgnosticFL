# Contributing

Thank you for your interest. No license has been chosen for this repository yet (see the [README](README.md#license)), so please open an issue to discuss a change before you send a larger pull request.

## Development setup

Follow [getting started](docs/getting-started.md) for the virtual environment, then add pandas for the complete unit-test set:

```bash
uv pip install --python .venv/bin/python pandas
```

The framework adapters need their framework only where they run: `nvflare==2.9.0` for the NVFlare adapter tests, `flwr==1.39.0` for the Flower relay tests, `vantage6==4.15.2` to run the vantage6 adapter (its unit tests use a fake client and run without it). Use separate environments with the versions of [`deploy/versions.env`](deploy/versions.env), as the testbed does.

## Tests

```bash
python -m pytest                                    # unit tests; framework tests skip without their framework
python -m pytest tests/conformance --adapter mock   # conformance suite against an in-process mock adapter
python -m flhub_core.selfcheck                      # determinism check: compare the digest with the reference
```

- Continuous integration ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) runs the selfcheck, the unit tests and the mock conformance suite on every pull request and on `main`.
- Changes to `flhub_core` change the content hash in `flhub_core.code_version()`, which every site reports. Keep training deterministic and check `selfcheck` on every platform you support; a changed digest means sites can no longer reproduce each other bit for bit.
- Changes to an adapter must pass the conformance suite against a running deployment of that framework: `python -m pytest tests/conformance --adapter http://127.0.0.1:<port> -v -s` (see the framework guide for the sites it accepts).
- Changes to the orchestrator or an aggregator must keep runs equal to the simulation: `python -m hub.sim --config <config> --compare <run dir>` must report `hashes_equal=True`.

## Adding a framework or an aggregator

- New framework adapter: [docs/adding-a-framework.md](docs/adding-a-framework.md). The orchestrator, the hub core and the protocol must not need changes; the conformance suite is the admission test.
- New server aggregator: [docs/aggregators.md](docs/aggregators.md#adding-an-aggregator).
- Protocol changes need a decision record in [docs/design/decisions.md](docs/design/decisions.md) and a version bump; new fields must be optional, so that older adapters keep working.

## Documentation style

- Plain, precise English, short sentences, active voice.
- Every number cites its evidence file (under `docs/results/evidence/`) or the command that produced it. Units are explicit: MiB and GiB for sizes and memory, seconds per round for times.
- Use placeholders such as `<central-host>` instead of real addresses; never put credentials, keys or tokens in a document.
- Run `python scripts/docs/check_links.py` before you push: every relative link and image must resolve, and every figure in `docs/results/figures/` must be used.

## Commits and pull requests

- One logical change per commit, small commits. Message: `<scope>: <imperative summary>`, at most 72 characters, for example `orch: weight FedAvg by num_examples` or `docs: add the Flower guide`. Scopes in use: `core`, `hub`, `orch`, `proto`, `nvflare`, `v6`, `flower`, `deploy`, `data`, `tests`, `docs`, `scripts`, `tools`.
- Never commit secrets, startup kits, API keys, datasets, model weights or run directories; `.gitignore` covers the usual paths.
- Fill in the pull-request template: what changed, how it was tested, which documents changed.

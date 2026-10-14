# Role: FLOWER — Flower adapter + Flower hospitals (145.38.187.20)

You make Flower hospitals `flw-h5` and `flw-h6` take part in hub rounds. Hospital side = standard Flower SuperNodes running a standard `ClientApp`. Server side (central VM) = Flower SuperLink + a long-running relay `ServerApp` + the adapter service.

## P0 — Bootstrap

- [ ] `tools/bus health`; post `info` "flower agent up" with VM specs.
- [ ] Verify commit attribution and hooks (COLLABORATION.md §3).
- [ ] `nc -zv 145.38.205.58 9092` and `7700`; report gaps.
- [ ] Create `~/venvs/flower` (Python 3.11) with the Flower version from `deploy/versions.env` + torch CPU.
- [ ] With `scout`, study in the INSTALLED version: `flower-superlink` flags and ports (ServerAppIo 9091, Fleet 9092, Control 9093, `--insecure`), `flower-supernode` (`--superlink`, `--node-config`, `--clientappio-api-address`, isolation mode), `flwr run` and how federations/SuperLink connections are configured, the Message API (`ServerApp`, `Grid.get_node_ids`, `Grid.send_and_receive`, `Message`, `RecordDict`, `ArrayRecord`, `ConfigRecord`, `MetricRecord`, `ClientApp` with `@app.train()`), and how a FAB is built/shipped. 15-line summary in your worklog.

## P1 — Island (on your VM; goal: M1-flower)

- [ ] Wait for M0-core, `git pull --rebase`, install `flhub_core` and `adapters/common`.
- [ ] `clients/flower/` (Flower app project, pyproject with `[tool.flwr.app.components]`):
  - `client_app.py`: standard `ClientApp`, `@app.train()`: read `arrays` + `config` → `flhub_core.train` → reply `ArrayRecord` + `MetricRecord({"num-examples": n, "train_loss": …})` and the site id from `context.node_config["site-id"]`. Data path from `node_config`.
  - `server_app.py`: the relay `ServerApp` (it is part of the same FAB but only runs on the central VM): loop: poll adapter `GET 127.0.0.1:8813/relay/next` → map site ids to node ids → `grid.send_and_receive(train messages)` → post replies to `/relay/result`. Exit on `shutdown`.
- [ ] `adapters/flower/service.py`: flhub/0.1 JSON-RPC on :8813 + `/relay/*`; ensures the relay run is active (`flwr run` against the local Control API) and that both SuperNodes are connected (`discover`).
- [ ] Island: local SuperLink, 2 local SuperNodes (`site-id=flw-h5`/`flw-h6`, partitions 4 and 5, distinct ClientAppIo ports), local hub core + orchestrator. Pass the conformance test and a 3-round run.
- [ ] Scripts:
  - `deploy/central/flower/{install,start,stop,status}.sh` — on the central VM: venv, SuperLink (Fleet public :9092, ServerAppIo/Control local), adapter :8813, start the relay run.
  - `deploy/flower-vm/{install,start,stop,status}.sh` — on your VM: venv, materialize partitions, start both SuperNodes against `145.38.205.58:9092`.
- [ ] Announce **M1-flower** with evidence.

## P2 — Integration

- [ ] Send a `deploy-request` to central.
- [ ] On PASS: stop island services, start both SuperNodes against the central SuperLink. Confirm via the adapter's `discover`.
- [ ] Support the `flower-only` and mixed runs.
- [ ] Optional stretch (only after M3-final, if time allows): TLS + SuperNode authentication; record as ADR.

## P3/P4

- [ ] Keep SuperNodes on the frozen SHA during final runs; watch logs.
- [ ] On `final-call`: write `docs/report/flower.md` (what a hospital installs/configures, problems solved). Push, `report-ready`.
- [ ] On `all-done`: verify your DoD items, final worklog entry, push, `touch ~/.flhub/DONE`.

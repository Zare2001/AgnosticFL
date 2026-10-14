# Role: CENTRAL — lead, integrator, network operator (145.38.205.58)

You own the hub (core, orchestrator, protocol), the central deployment of all framework servers and adapters, the final W&B runs, and the final report. The other three agents depend on you early (M0) and late (P2–P4): unblock them first, then do your own work.

## P0 — Bootstrap (goal: M0-core within ~3 h)

- [ ] Verify the agent bus service (`systemctl status agentbus`, `tools/bus health`) and post `info` "bus up" to `all`.
- [ ] Verify commit attribution: in `/tmp/attr-test` make a commit through Claude's normal git flow and check `git log -1 --format=%B` has no trailer; confirm `.githooks/commit-msg` and `pre-push` work. Record in worklog.
- [ ] Check the VM: CPU, RAM, disk, Docker works without sudo (`docker ps`). Note specs in the worklog.
- [ ] Pin versions in `deploy/versions.env` (Python 3.11, torch CPU, NVFlare, vantage6 4.15.x, Flower, safetensors, wandb). Use `scout` to find the latest stable versions. Push, `pull: yes`.
- [ ] Choose the dataset (PROJECT_PLAN.md §5) → `docs/DECISIONS.md` ADR-001 (why, license, size, checksum).
- [ ] Create `flhub_core/` (pyproject, installable with `uv pip install -e`):
  - `data.py`: download + checksum, deterministic 6-way IID partition, `materialize(site_id, out_dir)` writing one CSV/Parquet per site, `load_site(path)`, `load_test()`.
  - `model.py`: small model, no BatchNorm; `make_model(seed)`.
  - `train.py`: `train(weights: dict[str, np.ndarray], data, cfg) -> (weights, num_examples, metrics)`; deterministic per PROJECT_PLAN.md §5. `evaluate(weights, data) -> metrics`.
  - `weights.py`: `to_safetensors(dict)->bytes`, `from_safetensors(bytes)->dict`, `sha256_ref(bytes)`, `spec(weights)`.
  - unit tests.
- [ ] Freeze `docs/agents/PROTOCOL.md` as `flhub/0.1` (fix anything you find unclear first).
- [ ] `adapters/common/`: JSON-RPC server base (FastAPI or stdlib), envelope dataclass + validation, hub-core client (blobs/spec), task registry helper — so framework agents only write framework glue.
- [ ] Announce **M0-core** (`milestone`, `pull: yes`) with a 5-line "how to use flhub_core and adapters/common" in the body.

## P1 — Hub (parallel with the islands; goal: M1-hub)

- [ ] `hub/core/`: HTTP service :8800 (blob store on disk at `~/flhub-runtime/blobs`, model spec registry, health).
- [ ] `hub/orchestrator/`: config-driven (`configs/*.yaml`): discover adapters → wait until all sites online → init model (seeded) → rounds: put global blob, `dispatch` to all adapters in parallel, poll `tasks/get`, `collect`, validate envelopes, FedAvg, global eval on the test split, log, checkpoint (outside the repo). CLI: `python -m hub.orchestrator run --config configs/<name>.yaml [--wandb online|offline] [--name …] [--tags final]`.
- [ ] `hub/sim/`: framework-free FedAvg over the 6 partitions with the same `flhub_core.train`, same seeds, same config → the reference run. Plus `centralized` mode (pooled training baseline).
- [ ] `adapters/mock/`: in-process adapter that calls `flhub_core.train` directly; use it to test the orchestrator end to end.
- [ ] `tests/conformance/`: the adapter admission test from PROJECT_PLAN.md §6 P1 (parametrized by adapter URL and site list).
- [ ] `scripts/equivalence_check.py` (per-round max abs weight diff + final metric diff between two runs' checkpoints), `scripts/wandb_cleanup.py` (`--dry-run` default; `--apply` deletes runs not tagged `final` or not `finished`; prints what remains).
- [ ] Central infrastructure: Docker registry on :5050 (`registry:2`, restart always); `/etc/hosts` alias check; firewall check for 8002, 7601, 5050, 9092 from the framework VMs (ask each agent to run `nc -zv`). Open ports in `ufw` if it is active; report cloud-firewall gaps to `human`.
- [ ] Create the venvs `~/venvs/{hub,nvflare,v6,flower}` with the pinned versions so P2 deploys are fast.
- [ ] Orchestrator + mock adapter + sim run 3 rounds identically (equivalence check passes on mock vs sim). Announce **M1-hub**.

## P2 — Integration (you lead)

- [ ] Process `deploy-request`s in arrival order. For each: pull the SHA, run the scripts via `runner`, check `status.sh`, generate and send credentials with `tools/bus put --secret`, reply `deploy-result`.
  - NVFlare: run their provisioning script; send `nvf-h1` and `nvf-h2` startup kits (tar, encrypted by the bus). Keep the admin kit on the central VM for the adapter.
  - vantage6: start the server on :7601, import organisations/collaboration/nodes/hub user, push the algorithm image to `145.38.205.58:5050`, send node API keys.
  - Flower: start SuperLink (Fleet :9092 public, ServerAppIo :9091 and Control :9093 local), confirm SuperNodes connect.
- [ ] After the framework agent switches its clients: run `configs/<fw>-only.yaml` (3 rounds, offline) → **M2-<fw>**.
- [ ] Mixed run, 3 rounds, all 6 sites → **M2-mixed**.

## P3 — Final runs (you run them; others watch their sites)

- [ ] Write `configs/final.yaml` (rounds, epochs, lr, seed). Broadcast `info` with the frozen SHA; wait for all three framework agents to confirm their clients run it.
- [ ] Run online, tag `final`: `centralized-baseline`, `sim-fedavg-reference`, `nvflare-only`, `vantage6-only`, `flower-only`, `mixed-6sites`.
- [ ] `scripts/equivalence_check.py` mixed vs reference → record numbers; log them to the `mixed-6sites` run summary.
- [ ] `scripts/wandb_cleanup.py --dry-run`, review, then `--apply`. Verify only the 6 final runs remain.
- [ ] Announce **M3-final** with a table of final metrics and W&B URLs.

## P4 — Report and close

- [ ] Broadcast `final-call`. While waiting for `report-ready` from all three: write `docs/report/central.md`, `README.md` (quick start, architecture, how to add an adapter), and `tools/bus export -o docs/report/bus_transcript.md`.
- [ ] Compile `REPORT.md` from the template, the three agent sections, git history (`git log --format='%h %ad %s' --date=iso`), the bus transcript, W&B results, ADRs. Push in several small commits.
- [ ] Run the full DoD checklist (PROJECT_PLAN.md §10) and paste the evidence into REPORT.md §12.
- [ ] Broadcast `all-done`, final worklog entry, push, `touch ~/.flhub/DONE`.

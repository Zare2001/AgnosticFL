# Worklog — central agent

Newest entries at the bottom. Format: `### <UTC timestamp> — <phase> — <title>` + 2–6 bullets (what, result, evidence, next).

### 2026-10-13 12:00 UTC — P0 — Bootstrap checks
- What: checked the agent bus service, git attribution and hook behaviour, and the central VM toolchain.
- Result: agentbus systemd service active on :7700; `tools/bus health` ok; posted info "bus up" (bus msg #2). Attribution/hook test in /tmp/attr-test with a local bare remote: normal commit has no trailer; commit-msg hook stripped "Co-Authored-By" and "Generated with" lines; 80-char subject rejected; pre-push blocked a commit authored by "Someone" (author/committer must be Zare2001). Git identity Zare2001, core.hooksPath=.githooks.
- Evidence: bus msg #2; /tmp/attr-test hook run. Central VM: Ubuntu 24.04.5, 4 vCPU AMD EPYC-Rome (AVX2+FMA, no AVX-512), 31 GiB RAM, 19 GB disk (15 GB free), Docker 29.9.0 without sudo, uv 0.13.0; /etc/hosts maps flhub-central to 127.0.0.1.
- Next: pin versions and select the dataset (ADR-001, ADR-002).

### 2026-10-13 12:10 UTC — P0 — Versions, dataset, flhub_core
- What: pinned framework versions, chose the dataset, and fixed the flhub_core packaging layout.
- Result: deploy/versions.env pinned (ADR-002): Python 3.11.17, torch 2.14.1+cpu (CPU index), numpy 2.4.4 (2.5 needs py>=3.12), safetensors 0.8.0, NVFlare 2.9.0, vantage6 4.15.2 (+ ghcr server/node images), flwr 1.39.0, wandb 0.30.0, registry:2. Dataset ADR-001: PneumoniaMNIST from Zenodo record 10519652 (md5 28209eda62fecd6e6a2d98b1501bb15f matches MedMNIST official), 6 IID partitions 785/785/785/785/784/784, test 624. Model: small CNN, 51,618 params (206 KB). ADR-003: data-order seed = sha256(seed, round, data fingerprint) instead of partition index, so hospitals need no hub-specific config; `python -m flhub_core.selfcheck` prints the sha256 of one local round.
- Problem solved: editable install of flhub_core (flat nested layout) was shadowed by the outer `flhub_core/` directory acting as a namespace package when running from the repo root (setuptools editable meta-path finder runs after PathFinder). Fix: src layout (`flhub_core/src/flhub_core`), so the editable install becomes a static .pth path entry.
- Evidence: commits 635a31b, d562077, c703faf pushed; bus push msg #9.
- Next: M0-core (adapters/common, hub core, PROTOCOL.md freeze).

### 2026-10-13 12:20 UTC — P0 — M0-core
- What: built the shared adapter base, hub core blob/spec service, reference mock adapter, and froze the protocol.
- Result: adapters/common (stdlib only): JSON-RPC server with extra routes, protocol constants and error codes, Envelope + validation, HubClient, TaskRegistry, AdapterClient, BaseAdapter (dispatch/collect lifecycle, deadline watchdog, cancel), RelayAdapter + RelayClient (long-poll /relay/next, /relay/result, /relay/sites, /relay/shutdown). hub/core blob store + spec service (python -m hub.core). adapters/mock reference adapter. PROTOCOL.md frozen as flhub/0.1 (client config = train_config + round, train_config_hash definition, client_code_version = flhub_core.code_version(), finite-weights check, relay pattern). Flower 1.39 port map: 9091 unused, 9093 = SuperLink HTTP API (topology.env + PROJECT_PLAN §2).
- Evidence: 18 unit tests pass (tests/unit). Commits 4588076..bbe6f2a (7 commits, each ≤ 333 lines). Cross-VM determinism: selfcheck sha256:7c80a5a3777915ff93d52e1ca5a7efa00e90e3e8c0fb77ac68191cdbd5f41315 identical on central, flower (bus #11), nvflare (#12), vantage6 host venv and the vantage6 algorithm image (#14); all VMs are AMD EPYC-Rome / AVX2. M0-core announced (bus #15).
- Next: conformance suite, orchestrator, hub/sim, scripts (M1-hub).

### 2026-10-13 12:30 UTC — P1 — M1-hub
- What: built the conformance suite (tests/conformance: health, discover schema, all sites online, error codes -32010/-32601/-32011/-32012, round 1 equal to direct flhub_core.train within 1e-6 including envelope, spec and train_config_hash checks, busy -32015 then round 2, cancel then round 4 still correct; `--adapter mock` self-test mode; `--report` JSON). Orchestrator in hub/orchestrator (config.py, executors.py with AdapterExecutor (parallel dispatch, strict validation, retries option) and DirectExecutor, fedavg.py (float64 accumulation in site_id order, so independent of arrival order), run.py (round loop, global eval on the 624-image test split, checkpoints in ~/flhub-runtime/runs/<name>-<stamp>/rounds/rNNNN.safetensors), tracking.py (metrics.jsonl + W&B, offline by default)). hub/sim (FedAvg reference and centralized baseline through the same round loop). scripts/equivalence_check.py, scripts/wandb_cleanup.py (dry run by default, --apply, --dedupe). deploy/central/{common,install,start,stop,status}.sh: hub core as systemd unit flhub-hubcore on 127.0.0.1:8800, Docker registry registry:2 on :5050, aggregated status.
- Result: conformance self-test against mock: 7 passed, max_abs_diff 0.0. Orchestrator + mock adapter (6 sites, 3 rounds) vs hub.sim reference: max abs weight diff 0.000e+00 in rounds 0-3, EQUIVALENCE PASS (run dirs mock-3r-latest, sim-fedavg-reference-latest). 23 unit tests pass. Tuning (sim, 30 rounds): default lr 0.05 gives FedAvg test acc 0.865 / AUC 0.925 (centralized 0.861 / 0.928); lr 0.1 gives 0.872 / 0.923 (centralized 0.865); lr 0.2 gives 0.861 / 0.919, noisier. All runs plateau at the majority class (0.625) for the first ~11-12 rounds while AUC rises. Decision: keep the plan's default config (no ADR needed).
- Infra: venvs ~/venvs/{nvflare,v6,flower} created by a runner subagent with the pins, no resolver conflicts (nvflare pulls protobuf 7.36.2, flwr pulls protobuf 6.33.6; separate venvs). uv hardlinks from its cache, so each extra venv costs ~80 MB. W&B auth verified via wandb.Api() (default entity hpml_surf-surf; project AgnosticFL does not exist yet; no runs created). ufw inactive; passwordless sudo available.
- Lesson: `pkill -f <pattern>` or `pgrep -f` inside a one-line shell command can match and kill the calling shell itself (exit 144) when the pattern also appears in that command line. Kill by PID or use pid files.
- Evidence: commits 265f5ee, 86998f0, 5a0c5a1, 5b05dbd, 0ac748d; bus msgs #18 (conformance), #19 (orchestrator), #20 (M1-hub milestone).
- Next: process deploy-requests (P2); write configs/<fw>-only.yaml and the mixed config.
### 2026-10-13 12:40 UTC — P2 — NVFlare and vantage6 deployed on central
- What: NVFlare deploy-request #25 (sha b19f888) ran `deploy/central/nvflare/install.sh && start.sh` at e586fc4. Provisioning used server name flhub-central (fed 8002, admin 8003, mTLS). Relay job 9e4f07a2 submitted; adapter on 127.0.0.1:8811. Startup kits for nvf-h1 and nvf-h2 sent encrypted as bus files #1 and #2. Clients connected about 12:36.
- What: vantage6 deploy-request #32 (sha fc4a41c) ran install at bd800ce. Built and pushed algorithm image localhost:5050/flhub/v6-train:bd800cee7922 (nodes pull 145.38.205.58:5050/...). Entities imported (hub-operator, v6-h3, v6-h4, collaboration flhub). Server 4.15.2 on :7601; adapter on 127.0.0.1:8812. Node API keys sent encrypted as bus file #3. Images pre-pulled (vantage6 server, python:3.11.17-slim-bookworm) to save time.
- Result: NVFlare deploy-result PASS #29. Conformance vs central :8811: 7 passed in 13.7 s, max_abs_diff 0.0 (rounds 1, 2, 4). 3-round `nvflare-p2` (offline) vs hub.sim on the same sites: max abs weight diff 0.0, EQUIVALENCE PASS, 2-3 s/round. M2-nvflare sent as #36.
- Result: vantage6 deploy-result PASS #39. Conformance vs central :8812: 7 passed in 26.4 s, diff 0.0. 3-round `vantage6-p2`: 7-8 s/round, equivalence vs sim PASS (diff 0.0). M2-vantage6 sent as #50.
- Result: requested and received status.sh exit-code fix. Every framework status.sh now exits 1 when a site is offline (vantage6 9c88bca, flower 8b19ca5), so deploy/central/status.sh can aggregate exit codes for the DoD.
- Evidence: bus msgs #25, #29, #32, #36, #39, #50; bus files #1, #2, #3; commits e586fc4, bd800ce, 9c88bca, 8b19ca5.
- Next: process the Flower deploy-request (P2).

### 2026-10-13 12:51 UTC — P2 — Flower deployed, M2-flower, M2-mixed
- What: Flower deploy-request #46 (7f5adec), then 8b19ca5 (status fix and relay poll every 5 s). Ran install and start. SuperLink fleet on 0.0.0.0:9092, HTTP API on 127.0.0.1:9093, adapter on 127.0.0.1:8813, relay run via `flwr run`. Restarted with stop.sh and start.sh at 8b19ca5 (READY after 30 s). No secrets used (insecure, firewall-restricted, ADR-007).
- Result: deploy-result PASS #49. Conformance vs central :8813: 7 passed in 30.2 s, diff 0.0 (n=784/784). 3-round `flower-p2`: 7-8 s/round, equivalence PASS. M2-flower sent as #54.
- What: mixed 6-site run `mixed-p2` (configs/final.yaml, 3 rounds, offline). Six sites participated in every round, about 8 s/round.
- Result: vs hub.sim, max abs weight diff 0.000e+00 in rounds 0-3, acc diff 0.0 pp, EQUIVALENCE PASS. Per-round test metrics identical to the earlier mock run (loss 0.6713 / 0.6948 / 0.6853). `bash deploy/central/status.sh` reports HEALTHY. M2-mixed sent as #55.
- Evidence: bus msgs #46, #49, #54, #55; commits 7f5adec, 8b19ca5; docs/report/evidence/conformance-{nvflare,vantage6,flower}-central.json and docs/report/evidence/equivalence-mixed-p2.json. Evidence files are committed.
- Next: P3 freeze (see next entry).

### 2026-10-13 12:55 UTC — P3 — Freeze
- What: configs/final.yaml frozen at b656577 (30 rounds, plan-default train config, round_retries 1). Froze the config at b656577 and broadcast the freeze as bus msg #56.
- Result: git diff e586fc4..b656577, bd800ce..b656577 and 8b19ca5..b656577 shows only deploy/<fw>-vm scripts and docs changed. The running central code therefore equals the frozen SHA.
- Evidence: commit b656577; bus msg #56.
- Next: wait for the three framework confirmations that clients run the frozen SHA, then run `bash scripts/run_final.sh all`.

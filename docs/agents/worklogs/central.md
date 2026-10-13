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

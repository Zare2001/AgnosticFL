# Stage 2 worklog — nvflare agent

Newest entries at the bottom. Format: `### <UTC timestamp> — S<n> — <title>` + 2–6 bullets (what, result, evidence, next).

### 2026-10-13T13:52Z — S0 — kickoff: health, disk, hospital venv pinned
- Read PLAN.md, EXPERIMENTS.md, roles/NVFLARE.md, stage-1 worklog tail. `deploy/nvflare-vm/status.sh`: nvf-h1/nvf-h2 up, connected to flhub-central:8002 = 145.38.205.58, 1 job process each, HEALTHY (exit 0). Disk 14G free (uv cache pruned, nothing else to clean); RAM 29G available. Bus info #99.
- Finding: the hospital venv had `flhub_core` as an EDITABLE install (`__editable__.flhub_core-0.1.0.pth` -> /home/zpalanciya/AgnosticFL/flhub_core/src). A `git pull` on this VM would silently change hospital training code at the next job start (stage-1 artifact).
- Fix (ops, ledger row): reinstalled `flhub_core` from `git archive stage1-final flhub_core` as a normal (non-editable) install; `install.sh` does the same via `FLHUB_CORE_REF` (default stage1-final). Same code_version 0.1.0+9c6b064c1e96, selfcheck result sha256:7c80a5a3777915ff… (= stage 1). Clients not restarted, still HEALTHY.
- Next (S1): payload research, custom-code import order (rollout ADR), dynamic provisioning for nvf-h7/h8, native FedAvg (E7), inject/straggler/netem tools, purge.sh; flhub/0.2 adapter once central specifies it.

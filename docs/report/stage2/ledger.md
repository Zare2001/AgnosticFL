# Stage 2 — hospital-side change ledger

Every change on the hospital side (framework VMs, or anything a hospital would have to do) during stage 2. Each framework agent maintains its own rows (PLAN.md §4.6). Type: `code` | `config` | `data` | `ops`.

| UTC | framework | type | what | why | lines | restart | ADR |
|---|---|---|---|---|---|---|---|
| 2026-10-13T13:52Z | nvflare | ops | hospital venv `~/venvs/nvflare`: `flhub_core` 0.1.0 reinstalled as a fixed copy of `stage1-final` instead of an editable link to the VM's git checkout (same files: code_version 0.1.0+9c6b064c1e96, selfcheck sha256:7c80a5a3… unchanged); `deploy/nvflare-vm/install.sh` now installs it that way | a `git pull` on the VM must not change hospital code; flhub_core 0.2 must reach the hospitals only through NVFlare's own channel (job custom code) | 0 in `clients/` (install.sh: 7) | no (running job processes keep their loaded modules) | — |

## `git diff --stat stage1-final..HEAD -- clients/` (filled in by central at the end)

```
(pending)
```

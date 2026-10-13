# Architecture decision records

One ADR per decision that deviates from or fills a gap in docs/agents/PROJECT_PLAN.md.
Format: `## ADR-<n>: <title>` with **Context**, **Decision**, **Consequences** (≤ 12 lines each), plus date and author role.

<!-- ADR-001 (dataset choice) is written by the central agent in phase 0. -->

## ADR-001: Dataset = PneumoniaMNIST (MedMNIST v2), one CSV per site
Date 2026-10-13, central.
**Context**: PROJECT_PLAN.md §5 needs a public, login-free, health-related dataset that trains to a meaningful score on CPU in minutes and fits one CSV/Parquet file per site.
**Decision**: PneumoniaMNIST (Yang et al., MedMNIST v2, CC BY 4.0; source: Kermany et al. chest X-rays). File `pneumoniamnist.npz` from Zenodo record 10519652 (4.17 MB, md5 `28209eda62fecd6e6a2d98b1501bb15f`, sha256 `e1792d3f03751cb101e99f19a63b3c1941436c988665f47853417b05be250cd8`). 28x28 grayscale, binary (normal / pneumonia).
Official train split (4,708) → 6 IID partitions with `RandomState(2026).permutation` (frozen numpy stream): 785/785/785/785/784/784 for nvf-h1, nvf-h2, v6-h3, v6-h4, flw-h5, flw-h6. One CSV per site (`label,x0..x783`, integer pixels), sha256 of every site file pinned in `flhub_core.data.PARTITION_SHA256`. Test split (624, 62.5 % pneumonia) stays on the central VM for global evaluation; the val split is unused.
Model: CNN conv(1→8)→pool→conv(8→16)→pool→fc(784→64)→fc(64→2), ReLU, no normalization layers; 51,618 parameters, 206 KB float32.
**Consequences**: tiny payloads and ~1 s per local round per site on one CPU thread; binary task gives accuracy + AUC. MedMNIST's test split is shifted against train (published ResNet-18 test acc ≈ 85 %), so expect ~85 % rather than the ~95 % validation accuracy. The CDC diabetes table was rejected because its accuracy barely beats the 86 % majority baseline.

## ADR-002: Version pins (deploy/versions.env)
Date 2026-10-13, central.
**Context**: the plan asks for latest stable versions, Python 3.11 and vantage6 4.15.x.
**Decision**: Python 3.11.17 (uv), torch 2.14.1+cpu, numpy 2.4.4, safetensors 0.8.0, NVFlare 2.9.0, vantage6 4.15.2, flwr 1.39.0, wandb 0.30.0, registry:2. numpy 2.5 requires Python ≥ 3.12, so 2.4.4 is the newest usable; vantage6 5.x (latest 5.0.3, Python ≥ 3.13, Kubernetes) is excluded by the plan.
**Consequences**: every VM and the vantage6 image install torch from the CPU index with the exact same version, a precondition for bit-identical training.

## ADR-003: Determinism without hospital-side configuration
Date 2026-10-13, central.
**Context**: §5 seeds the data order with (seed, round, partition_index). A real hospital has no "partition index", and passing one would be hub-specific client config.
**Decision**: the data order is seeded with sha256(`seed`, `round`, data fingerprint), where the fingerprint is the sha256 of the site's canonical uint8 pixels + int64 labels. Same data => same order, with zero configuration; the reference simulation reproduces it by loading the same files. Training uses 1 CPU thread, `use_deterministic_algorithms(True)`, a fresh SGD (foreach=False) per call, no dropout. `python -m flhub_core.selfcheck` prints the sha256 of one local round; all VMs and the vantage6 image must print the same value.
**Consequences**: bit-exact cross-framework FedAvg is possible if all CPUs take the same kernel paths (central VM: AMD EPYC-Rome, AVX2). If selfcheck differs across VMs, the fallback is pinning `ATEN_CPU_CAPABILITY`/`MKL_CBWR`/`ONEDNN_MAX_CPU_ISA` (new ADR).

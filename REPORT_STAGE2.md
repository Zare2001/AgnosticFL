# AgnosticFL — Stage 2 report: full system and performance test

Stage 2 tests the stage-1 system (`REPORT.md`, tag `stage1-final`) under realistic and demanding conditions: non-IID and uneven data, six server aggregators, larger models, payloads up to 512 MB, more sites, dropouts, stragglers, partial participation, hub crashes and framework-server restarts. It also measures where the time goes. The same four autonomous agents ran it (central, nvflare, vantage6, flower), one per VM, coordinating over the agent bus and one GitHub repository.

> Status: draft during S4. Sections marked *(pending)* are filled from the final runs in `docs/report/stage2/runs.md` and the generated tables (`docs/report/stage2/tables.md`, `scripts/stage2/report_tables.py`).

## 1. Executive summary

*(pending: RQ1–RQ5 answers, headline claim, campaign time, number of final runs, three main limits)*

## 2. Setup

### 2.1 What was reused and what is new

Reused from stage 1 without rebuilding: the four VMs, the hub core, the three framework servers on central (NVFlare 2.9.0, vantage6 4.15.2, Flower 1.39.0), the three adapters, the six hospitals (`nvf-h1`, `nvf-h2`, `v6-h3`, `v6-h4`, `flw-h5`, `flw-h6`) with their standard clients, the agent bus and the git workflow.

New in stage 2:

| Component | Change | Where |
|---|---|---|
| Protocol | flhub/0.2: per-site progress, partial collect, per-site cancel, per-run readiness, payload cap ≥ 1024 MB; a 0.2 adapter still serves 0.1 callers | `docs/agents/PROTOCOL.md` §7, ADR-008, `adapters/common` |
| Shared training library | `flhub_core` 0.2: OrganAMNIST, partitions, lazy data variants, model zoo, FedProx μ, weight decay, timing metrics, selfcheck v2 | `flhub_core/`, ADR-009, ADR-010 |
| Aggregators | stateful plugins fedavg (stage-1 code path), fedavgm, fedadam, fedyogi, fedadagrad, fedprox; float64 state, checkpointed every round | `hub/aggregators/` |
| Orchestrator and sim | config schema 2, seeded participation, `min_sites` through partial collect, strict retries with online wait, bit-exact `--resume`, per-round hashes of weights and aggregator state, timing breakdown, multi-class and site-shaped evaluation, no live W&B; sim `--workers`, `--replay`, `--participants-from`, `--compare` | `hub/orchestrator/`, `hub/sim/` |
| Campaign tooling | config generator, tuner, run driver, post-processing worker, finalize gate, E4a sweep, central E6 injector, sysmon, netem | `scripts/stage2/`, `scripts/sysmon.py`, `deploy/central/netem.sh` |
| Framework side | flhub/0.2 adapters, rollout of `flhub_core` 0.2 through each framework's own channel, E6 tools, E5 extra sites, E7 native FedAvg, purge scripts | `adapters/<fw>/`, `deploy/<fw>-vm/`, `deploy/central/<fw>/` |

### 2.2 Versions and machines

Versions are those of stage 1 (`deploy/versions.env`, ADR-002): Python 3.11.17, torch 2.14.1+cpu, numpy 2.4.4, safetensors 0.8.0, NVFlare 2.9.0, vantage6 4.15.2, flwr 1.39.0, wandb 0.30.0. `flhub_core` 0.2 has the content-hash code_version **`0.2.0+fe357a1dbec1`**; its selfcheck v2 digest is `sha256:ac074a0b79a2d3c6782765480164c0465ac598359dfe91695e893e208f4e5034` on central, in the NVFlare hospital runtime (job custom code), inside the vantage6 algorithm image `v6-train:a43cef3f75c0` and in the Flower SuperNode runtime (FAB). All four VMs: 4 vCPU AMD EPYC-Rome (AVX2), 31 GiB RAM, 19 GB disk. Training uses one CPU thread per site.

Frozen SHA for every final run: **`96c44c957d6ca5d3b9cc8af93dd227c62a9323d6`** (S3-frozen, bus #210). Frozen paths: `flhub_core adapters clients hub configs/stage2 deploy/central`.

### 2.3 Dataset (ADR-009)

**OrganAMNIST** (MedMNIST v2, 28 × 28 grayscale abdominal CT, 11 organ classes; CC BY 4.0). File `organamnist.npz` from Zenodo record 10519652: 38,247,708 bytes, md5 `68e3f8846a6bd62f0c9bf841c0d9eacc` (= MedMNIST's), sha256 `7c8c8b0753e2c0d7f70928817eac02be7cbb09f6beff4346e9d0ea79ecb598bd`. Official splits: train 34,561 (site partitions), validation 6,491 (S3 tuning, central only), test 17,778 (final evaluation, central only). Class counts in train: bladder 1,956, femur-L 1,390, femur-R 1,357, heart 1,474, kidney-L 3,963, kidney-R 3,817, liver 6,164, lung-L 3,919, lung-R 3,929, pancreas 3,031, spleen 3,561. The stage-1 PneumoniaMNIST partitions stay available as variant `pneumoniamnist-iid` (byte-identical to stage 1) for the regression test and the payload runs.

### 2.4 Partitions (ADR-009, `flhub_core/partition.py`)

All partitions are deterministic in `partition_seed` 2026 (numpy's frozen legacy RandomState, seeded from sha256(seed, purpose)); the training seeds 2026/2027/2028 never change them. Manifests with sizes, label histograms, CSV sha256 and data fingerprints are committed in `configs/stage2/partitions/`; every envelope reports `data_fp`, which the hub checks against the manifest.

![partitions](docs/report/stage2/figures/partitions.png)

| pid | definition | site sizes |
|---|---|---|
| `iid` | stratified, equal sizes | 5,761 / 5,760 × 5 |
| `dir1.0`, `dir0.3`, `dir0.1` | label skew, q_k ~ Dirichlet(α p), equal sizes; sites in order take round(q_k n_k) per class, an exhausted class passes its shortfall on in proportion to q_k | 5,761 / 5,760 × 5 |
| `qty` | IID labels (stratified), sizes ∝ 1:32:2:16:4:8 | 549, 17,555, 1,097, 8,777, 2,194, 4,389 |
| `fwcorr` | per class, framework shares ~ Dirichlet(0.3·1₃); IID halves inside a framework | NVFlare 3,219/3,218, vantage6 9,076/9,075, Flower 4,985/4,988 |
| `mix` | dir0.3 proportions with the qty sizes | as qty |
| `iid12` | stratified over 12 sites (E5) | 2,881 / 2,880 × 11 |

With equal site sizes and a small α the class pools run out, so the last sites in the order take the leftovers and `dir0.1` is only slightly more skewed than `dir0.3`; the realized histograms (figure) are what the experiments use.

### 2.5 Model zoo (ADR-010)

| id | architecture | parameters (11 classes) | payload (float32) | selfcheck v2 result |
|---|---|---|---|---|
| `cnn-small` | stage-1 CNN: conv8-pool-conv16-pool-fc64-fc | 52,203 | 0.2 MB | `sha256:d28ac252…` |
| `cnn-medium` | 2×conv32-pool-2×conv64-pool-fc512-fc | 1,676,779 | 6.4 MB | `sha256:e2a59ca5…` |
| `resnet18-gn` | ResNet-18, CIFAR stem, GroupNorm(32) | 11,173,323 | 42.6 MiB | `sha256:19e6b79b…` |
| `payload-<MB>` | synthetic float32 tensors of 4 MiB; "training" = w + 1e-3·c(seed, round, data fingerprint, tensor) | <MB> MiB / 4 | <MB> MiB | `sha256:f4cb4e9d…` (payload-8) |

Local training cost per site and round on 5,761 images with one thread: cnn-small 1.6 s, cnn-medium 11.4 s, resnet18-gn about 3.6 min.

### 2.6 Aggregators and tuned values

With p_k = n_k / Σ n_j over the participating sites and Δ = Σ_k p_k (w_k − x), all in float64 with one float32 cast of the new global model per round (PLAN.md §4.1): fedavg x ← Σ p_k w_k (the stage-1 code path); fedavgm m ← βm + Δ, x ← x + ηm; fedadam m ← β1 m + (1−β1)Δ, v ← β2 v + (1−β2)Δ², x ← x + η m/(√v + τ); fedyogi as fedadam with v ← v − (1−β2)Δ² sign(v − Δ²); fedadagrad v ← v + Δ², x ← x + ηΔ/(√v + τ); fedprox = fedavg at the server and μ(w − x) added to the client gradient (μ = 0 skips it). Defaults β 0.9, β1 0.9, β2 0.99, τ 1e-3, v₀ = τ².

S3 tuning (sim, validation split, dir0.3, 30 rounds; `docs/report/stage2/tuning.md`): client lr **0.1**; fedavgm η **1.0**, fedadam η **0.03**, fedyogi η **0.03**, fedadagrad η **0.03**, fedprox μ **0.01**. **A\* = fedavgm** (final validation accuracy 0.914). Four optima lie on the edge of the plan's grid.

## 3. RQ1 — Correctness (INV-1)

*(pending: per-run INV-1 table from runs.md, exceptions, E6d resume equality, INV-4 re-runs)*

## 4. RQ2 — Hospital-side cost

*(pending: ledger, git diff --stat of clients/ and adapters/, rollout comparison)*

## 5. RQ3 — Learning under heterogeneity (E1, E2, E3)

*(pending)*

## 6. RQ4 — Performance (E3, E4, E5, E7)

*(pending)*

## 7. RQ5 — Robustness (E6)

*(pending)*

## 8. Threats to validity

*(pending)*

## 9. Limitations, security notes, future work

*(pending)*

## 10. How the agents ran the campaign

*(pending)*

## 11. Reproduce

*(pending)*

## 12. Definition of Done — evidence

*(pending)*

## Appendix

*(pending: runs.md, ADR list, transcript, figure index)*

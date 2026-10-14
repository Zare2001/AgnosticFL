# Results

The system was tested in two stages on a reference testbed of four VMs (the hub and three framework servers on one, two simulated hospitals per framework on each of the others; [method](../cost-and-efficiency.md#11-method-and-caveats)).

- **[Stage 1](stage1.md):** one FedAvg hub across NVFlare, vantage6 and Flower; six hospitals, PneumoniaMNIST, 30 rounds.
- **[Stage 2](stage2.md):** the full system and performance test: non-IID data, six aggregators, three model sizes, payloads up to 512 MiB, 12 sites, faults, native baselines; 122 final runs.
- **[Cost and efficiency](../cost-and-efficiency.md):** the same results seen as costs, per framework.

## Headline numbers

| | Result | Source |
|---|---|---|
| Equivalence | Every compared real run is byte-identical to the framework-free FedAvg simulation: the stage-1 mixed run (31 of 31 checkpoints) and all 69 stage-2 real runs (difference 0.0 in every round, including the server-optimizer state) | [stage 1](stage1.md#results), [stage-2 tables](evidence/stage2/tables.md) |
| Federated vs centralized | Stage 1: 86.5 % accuracy / 0.925 AUC federated across three frameworks, against 86.1 % / 0.928 centralized on the pooled data | [final-runs.json](evidence/stage1/final-runs.json) |
| Hospital code changed | 0 lines in stage 2, while aggregators, data, models and participation changed on the hub | [hospital ledger](evidence/hospital-ledger.md) |
| Hospital steps per library update | 0 for all three frameworks (job custom code, image tag, FAB) | [hospital ledger](evidence/hospital-ledger.md) |
| Overhead against native FedAvg | −20 % to +10 % per round, at most +0.73 s | [stage-2 tables](evidence/stage2/tables.md) (E7) |
| Payload | 512 MiB per site and direction with NVFlare (15.5 s per round) and Flower (45.8 s); vantage6 up to 128 MiB (468 s), 256 MiB never delivered | [stage-2 tables](evidence/stage2/tables.md) (E4a) |
| Network | 1.002–1.06 bytes on the wire per byte of update with NVFlare and Flower; 5–9 with vantage6 | [e4_bytes.md](evidence/stage2/e4_bytes.md) |
| Scaling | 6 → 12 sites: +14 % round time | [stage-2 tables](evidence/stage2/tables.md) (E5) |
| Robustness | Hospital crashes, stragglers, hub crash and framework-server restarts all recovered without manual action; resume is bit-exact | [e6.md](evidence/stage2/e6.md) |
| Learning under skew | Label skew costs FedAvg 6–9 points; FedAvgM and FedYogi recover 2–4 | [stage-2 tables](evidence/stage2/tables.md) (E1, E2) |

## Figure gallery

All figures are in [`figures/`](figures/). The stage-1 and cost figures are drawn by [`scripts/docs/`](../../scripts/docs/) from evidence in this folder, the stage-2 figures by [`scripts/stage2/plots.py`](../../scripts/stage2/plots.py) from the run directories.

**Stage 1**

| Figure | What it shows |
|---|---|
| [stage1_accuracy_loss.png](figures/stage1_accuracy_loss.png) | test accuracy and loss per round of all six final runs |
| [stage1_final_metrics.png](figures/stage1_final_metrics.png) | final accuracy and AUC: federated, single-framework, simulation, centralized |
| [stage1_equivalence.png](figures/stage1_equivalence.png) | weight difference between hub and simulation per round (all 0.0) and the identical accuracy curves |
| [stage1_round_time.png](figures/stage1_round_time.png) | seconds per round per framework, mixed and references |

**Stage 2: learning**

| Figure | What it shows |
|---|---|
| [partitions.png](figures/partitions.png) | label histogram of every site in every data partition |
| [e1_accuracy_vs_round.png](figures/e1_accuracy_vs_round.png) | FedAvg test accuracy per round for each partition |
| [e1_final_and_worst_site.png](figures/e1_final_and_worst_site.png) | final and worst-site accuracy per partition |
| [e2_final_accuracy_grid.png](figures/e2_final_accuracy_grid.png) | final accuracy per aggregator and partition |
| [e2_worst_site_grid.png](figures/e2_worst_site_grid.png) | worst-site accuracy per aggregator and partition |
| [e2_dir01_accuracy_vs_round.png](figures/e2_dir01_accuracy_vs_round.png) | accuracy per round of every aggregator on the most skewed partition |

**Stage 2: performance and robustness**

| Figure | What it shows |
|---|---|
| [e3_round_time_breakdown.png](figures/e3_round_time_breakdown.png) | training, framework overhead and hub time per round for three model sizes |
| [e4a_round_time_vs_payload.png](figures/e4a_round_time_vs_payload.png) | round time against update size per framework |
| [e4a_peak_rss_vs_payload.png](figures/e4a_peak_rss_vs_payload.png) | peak memory per process against update size (the axis says MB; the values are MiB) |
| [e4b_measured_vs_bound.png](figures/e4b_measured_vs_bound.png) | fan-out time against the bandwidth lower bound, four link profiles |
| [e5_scaling.png](figures/e5_scaling.png) | 6 against 12 sites |
| [e6_timeline_s2-e6a-strict.png](figures/e6_timeline_s2-e6a-strict.png) | hospitals killed while all sites are required: detection, retry, recovery |
| [e6_timeline_s2-e6a-minsites.png](figures/e6_timeline_s2-e6a-minsites.png) | the same with `min_sites: 5` |
| [e6_timeline_s2-e6d-resume.png](figures/e6_timeline_s2-e6d-resume.png) | hub and adapters killed, then resumed |
| [e6_timeline_s2-e6e-server-restart.png](figures/e6_timeline_s2-e6e-server-restart.png) | framework servers restarted between and during rounds |
| [e7_native_vs_hub.png](figures/e7_native_vs_hub.png) | round time of each framework's own FedAvg against the hub |

**Cost and efficiency**

| Figure | What it shows |
|---|---|
| [cost_loc.png](figures/cost_loc.png) | lines of code per component |
| [cost_model_fit.png](figures/cost_model_fit.png) | fan-out time against update size with the fitted fixed cost and cost per MiB |
| [cost_bytes_on_wire.png](figures/cost_bytes_on_wire.png) | bytes on the wire per byte of update, per framework and size |

## Evidence

| Path | Content |
|---|---|
| [`evidence/stage1/`](evidence/stage1/) | per-round metrics of the stage-1 final runs, conformance reports, equivalence checks, the W&B run listing |
| [`evidence/stage2/runs.md`](evidence/stage2/runs.md) | all 122 stage-2 final runs: W&B link, metrics, equivalence method and difference |
| [`evidence/stage2/tables.md`](evidence/stage2/tables.md) | the generated result tables of every experiment (E1–E7, RQ1) |
| [`evidence/stage2/cost_model.md`](evidence/stage2/cost_model.md), [`e4_bytes.md`](evidence/stage2/e4_bytes.md), [`e6.md`](evidence/stage2/e6.md), [`tuning.md`](evidence/stage2/tuning.md) | cost model, bytes on the wire, robustness table, tuning grid |
| [`evidence/stage2/central-e4a-memory.md`](evidence/stage2/central-e4a-memory.md) | peak memory of the hub and framework-server processes per payload |
| [`evidence/stage2/nvflare/`](evidence/stage2/nvflare/), [`vantage6/`](evidence/stage2/vantage6/), [`flower/`](evidence/stage2/flower/) | per-framework evidence: hospital-VM memory, timing breakdowns, native-baseline comparisons, single-framework conformance |
| [`evidence/stage2/`](evidence/stage2/) `conformance-v2-*`, `inv4-*`, `endstate-central.txt`, `wandb_audit.json` | conformance v2 per adapter, reproducibility re-runs, end-state check, W&B audit |
| [`evidence/hospital-ledger.md`](evidence/hospital-ledger.md) | every hospital-side change of stage 2, with the `clients/` diff |
| [`evidence/loc.md`](evidence/loc.md) | lines of code per component |

The full stage reports are kept at their tags: [`REPORT.md` at `stage1-final`](https://github.com/Zare2001/AgnosticFL/blob/stage1-final/REPORT.md) and [`REPORT_STAGE2.md` at `stage2-final`](https://github.com/Zare2001/AgnosticFL/blob/stage2-final/REPORT_STAGE2.md).

# Reproducing the results

Every result of the two test stages is defined by a run configuration in [`configs/`](../configs/README.md), the code at a commit, and the site data written by `flhub_core.data`, which checks every file against a pinned sha256. The framework-free parts reproduce on any machine; the real runs need the framework servers and hospitals of the reference testbed ([`deploy/`](../deploy/README.md)).

## What you need

| To reproduce | You need |
|---|---|
| simulation results (references, extra seeds, tuning, equivalence) | the environment of [getting started](getting-started.md); about 1 GB for the OrganAMNIST variants |
| real runs | the testbed of [deployment](deployment.md#the-reference-testbed), `bash deploy/central/status.sh` reporting `HEALTHY` |
| tables and figures | the run directories (`~/flhub-runtime/runs/...`), or the evidence files already in [`docs/results/evidence/`](results/evidence/) |
| W&B uploads | a W&B key; uploads happen only after a run passed its equivalence check |

**Check first:** `python -m flhub_core.selfcheck` must print the digest `sha256:ac074a0b…` on every machine that trains; otherwise results can differ in the last bits and the equivalence checks need a tolerance.

## Stage 1

Configuration [`configs/final.yaml`](../configs/final.yaml): six sites on three adapters, PneumoniaMNIST, 30 rounds of FedAvg.

```bash
python -m hub.sim --config configs/final.yaml                      # framework-free FedAvg reference
python -m hub.sim --config configs/final.yaml --mode centralized   # centralized baseline, 30 epochs
python -m hub.orchestrator run --config configs/final.yaml --name mixed-6sites            # real run, testbed
python -m hub.orchestrator run --config configs/final.yaml --only nvflare --name nvflare-only   # likewise vantage6, flower
python -m hub.sim --config configs/final.yaml --compare <run dir of mixed-6sites>          # equivalence check
```

With the current code, the first two commands reproduce the stage-1 final models exactly: the simulation ends with the global model `sha256:7fd3e2a4…` (accuracy 0.8654, AUC 0.9247) and the centralized baseline with `sha256:88ed46d8…` (0.8606, 0.9279), the hashes recorded for the stage-1 runs in [final-runs.json](results/evidence/stage1/final-runs.json) (checked on the reference hardware during the documentation of this release). In stage 1 the orchestrator logged to W&B live and the runs were driven by [`scripts/run_final.sh`](../scripts/run_final.sh) with the stage-1 code (tag `stage1-final`); `scripts/equivalence_check.py` compares stage-1 run directories ([stage-1 results](results/stage1.md)).

## Stage 2

The configurations are in [`configs/stage2/`](../configs/stage2/), one file per final run, generated from `base.yaml` and `tuned.yaml` by [`scripts/stage2/make_configs.py`](../scripts/stage2/make_configs.py) (`--check` proves the committed files are current). The site topology is [`configs/stage2/topology.yaml`](../configs/stage2/topology.yaml).

**Data provisioning** at every hospital and on the hub, once:

```bash
python -m flhub_core.data materialize --variants all --sites <sites> --manifests configs/stage2/partitions
```

**One real run, its equivalence check, an extra seed** (the stage-2 pipeline, on the testbed's central machine):

```bash
python scripts/stage2/run_final.py real configs/stage2/e1/s2-e1-dir0.3-fedavg.yaml
python -m hub.sim --config configs/stage2/e1/s2-e1-dir0.3-fedavg.yaml --workers 4 \
       --compare ~/flhub-runtime/runs/stage2/s2-e1-dir0.3-fedavg-latest
python scripts/stage2/finalize_run.py ~/flhub-runtime/runs/stage2/s2-e1-dir0.3-fedavg-latest --mode real
python -m hub.sim --config configs/stage2/e1/s2-e1-dir0.3-fedavg.yaml --seed 2027 --workers 4
```

`run_final.py` refuses to start unless the hub's code equals the frozen commit and enough disk is free, announces the run to the testbed's machines and records system metrics; `finalize_run.py` uploads a run to W&B only after its equivalence check passed and appends its row to [runs.md](results/evidence/stage2/runs.md). The stage-2 runs used the frozen code at commit `975a949` (`96c44c9` for the payload sweep); the differences between the two are in the [stage-2 report](https://github.com/Zare2001/AgnosticFL/blob/stage2-final/REPORT_STAGE2.md) §11.

| Experiment | Configurations | Driver |
|---|---|---|
| E1 data heterogeneity, E2 aggregators | `configs/stage2/e1/`, `e2/` | `run_final.py` per run (`campaign_e1e2.sh`); extra seeds and references in the simulation |
| E3 model size | `configs/stage2/e3/` | `campaign_e3.sh`; `resnet18-gn` checked by replay (`hub.sim --replay <run dir> --rounds 1,2,3,last`) |
| E4a payload sweep | `configs/stage2/e4a/` | `e4a_sweep.py --scope <framework>` (purge the framework store first) |
| E4b bandwidth | `configs/stage2/e4b/` | `e4b_run.py` with `netem` on both ends of every hospital link |
| E5 sites | `configs/stage2/e5/` | `e5_run.py` (starts and stops the extra sites) |
| E6 robustness | `configs/stage2/e6/` | `campaign_e6.sh`, `inject_central.py`, `arm.py` and the hospital-side `inject.sh` / `straggler.sh` |
| E7 native baselines | `configs/stage2/e7/` | `campaign_e7.sh` with `deploy/central/<fw>/native/` |
| tuning grid | `configs/stage2/s3/` | `tune.py` (simulation only) |

The experiment drivers coordinate with the hospital machines through the testbed's message bus ([`tools/`](../tools/README.md)) and are written for the reference testbed ([`scripts/`](../scripts/README.md)).

**Tables and figures** from the run directories: `report_tables.py` ([tables.md](results/evidence/stage2/tables.md)), `cost_model.py`, `e4_bytes.py`, `e6_table.py`, `plots.py` and `plot_partitions.py` (the stage-2 figures), all in `scripts/stage2/`; the documentation's own tables and figures come from [`scripts/docs/`](../scripts/docs/) (stage-1 figures, cost figures, central memory, lines of code).

## The equivalence check (INV-1)

A real run passes if the simulation of the same configuration, seed and participants produces the same global weights after every round; the stage-2 orchestrator records the sha256 of the global weights and of the aggregator state per round, so the check compares hashes:

| Method | Command | Used for |
|---|---|---|
| full simulation | `python -m hub.sim --config <config> --compare <run dir>` | most runs (62 of 69 in stage 2) |
| recorded participants | the same; `--compare` replays the real run's participants (`--participants-from` without a comparison) | runs with dropped or sampled sites |
| replay | `python -m hub.sim --replay <run dir> --rounds 1,2,3,last` | long runs (`resnet18-gn`): selected rounds recomputed from the run's own checkpoints |
| resume equality | final weights and aggregator state hash-equal to the uninterrupted run | the resumed run |
| reproducibility (INV-4) | `python scripts/stage2/inv4_check.py <config>`: the real run again, compared round by round | two configurations, 3–5 hours apart |

All 69 stage-2 real runs and the stage-1 mixed run passed with a difference of 0.0 ([results](results/README.md)). The pass criterion allows 1e-5 per round, for hardware where bit-exactness does not hold.

## `configs/`, `scripts/` and `deploy/` at a glance

- [`configs/`](../configs/README.md): every run configuration; stage-2 files are generated.
- [`scripts/`](../scripts/README.md): run drivers, post-processing, checks, tables and figures.
- [`deploy/`](../deploy/README.md): the testbed's install, start, stop and status scripts, including the experiment tooling.

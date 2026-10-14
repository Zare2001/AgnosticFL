# scripts

Tools around the hub. None of them is needed to run a federation; they check, measure and reproduce the test stages.

| Path | What |
|---|---|
| `equivalence_check.py` | compares two run directories round by round (max weight difference, final accuracy); used for the stage-1 runs. For runs written by the stage-2 orchestrator use `python -m hub.sim --compare <run dir>` instead: the script's file pattern also picks up the aggregator-state files of those runs and stops |
| `sysmon.py` | samples CPU, memory, per-process RSS and network every second from `/proc`, and summarizes a run window (its "MB" are MiB) |
| `run_final.sh` | runs the six stage-1 final runs online to W&B, then the equivalence check |
| `wandb_cleanup.py` | keeps only finished runs tagged `final` in a W&B project (deletes the others when called with `--apply`) |
| `stage2/` | the stage-2 campaign: run driver and post-processing (`run_final.py`, `postproc.py`, `finalize_run.py`: equivalence gate, W&B upload, `runs.md` row), config generator and tuning (`make_configs.py`, `tune.py`), experiment drivers (`e4a_sweep.py`, `e4b_run.py`, `e5_run.py`, `inject_central.py`, `arm.py`, `campaign_*.sh`), result tables and figures (`report_tables.py`, `plots.py`, `plot_partitions.py`, `cost_model.py`, `e4_bytes.py`, `e6_table.py`), checks (`inv4_check.py`, `wandb_audit.py`, `endstate_central.sh`) and vantage6 maintenance (`v6_delete_tasks.py`, `v6_orphan_cleanup.sh`) |
| `docs/` | documentation tooling: lines of code (`loc.py`), link check (`check_links.py`), the stage-1 and cost figures (`plots_stage1.py`, `plots_cost.py`, `figstyle.py`) and the central memory table (`central_memory.py`). They read the repository or run directories and write only under `docs/` |

The stage-2 scripts were written for the reference testbed: they expect the services of [`deploy/`](../deploy/README.md), run directories under `~/flhub-runtime/runs/stage2`, and some coordinate with the hospital machines through the testbed's message bus ([`tools/`](../tools/README.md)). Scripts that upload to W&B need a W&B key in the environment. [Reproducing results](../docs/reproducing-results.md) shows which script produced which result.

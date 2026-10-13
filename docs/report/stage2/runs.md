# Stage 2 — final runs

Rows are appended by `scripts/stage2/finalize_run.py` (or by hand for native runs) only after INV-1 passed and the run was synced to W&B `AgnosticFL-stage2`.

| run | exp | mode | W&B | rounds | test acc | bal acc | macro AUC | median round s | inv1_method | inv1_max_diff | notes |
|---|---|---|---|---|---|---|---|---|---|---|---|
| s2-e4a-nvflare-1mb | E4 | real | [run](https://wandb.ai/hpml_surf-surf/AgnosticFL-stage2/runs/d2pkmgze) | 3 | - | - | - | 2.52 | full-sim | 0.000 |  |
| s2-e4a-nvflare-8mb | E4 | real | [run](https://wandb.ai/hpml_surf-surf/AgnosticFL-stage2/runs/iiz8gq2s) | 3 | - | - | - | 2.67 | full-sim | 0.000 |  |
| s2-e4a-nvflare-32mb | E4 | real | [run](https://wandb.ai/hpml_surf-surf/AgnosticFL-stage2/runs/cjb96tw3) | 3 | - | - | - | 2.66 | full-sim | 0.000 |  |
| s2-e4a-nvflare-64mb | E4 | real | [run](https://wandb.ai/hpml_surf-surf/AgnosticFL-stage2/runs/w81a7b83) | 3 | - | - | - | 3.67 | full-sim | 0.000 |  |
| s2-e4a-nvflare-128mb | E4 | real | [run](https://wandb.ai/hpml_surf-surf/AgnosticFL-stage2/runs/t528yqxl) | 3 | - | - | - | 5.08 | full-sim | 0.000 |  |
| s2-e4a-nvflare-256mb | E4 | real | [run](https://wandb.ai/hpml_surf-surf/AgnosticFL-stage2/runs/t4mcuesn) | 3 | - | - | - | 10.29 | full-sim | 0.000 |  |
| s2-e4a-flower-1mb | E4 | real | [run](https://wandb.ai/hpml_surf-surf/AgnosticFL-stage2/runs/m1fj6mt7) | 3 | - | - | - | 5.79 | full-sim | 0.000 |  |
| s2-e4a-flower-8mb | E4 | real | [run](https://wandb.ai/hpml_surf-surf/AgnosticFL-stage2/runs/puww1api) | 3 | - | - | - | 6.07 | full-sim | 0.000 |  |
| s2-e4a-flower-32mb | E4 | real | [run](https://wandb.ai/hpml_surf-surf/AgnosticFL-stage2/runs/1v1r4tf1) | 3 | - | - | - | 7.69 | full-sim | 0.000 |  |
| s2-e4a-flower-64mb | E4 | real | [run](https://wandb.ai/hpml_surf-surf/AgnosticFL-stage2/runs/5dcdngn5) | 3 | - | - | - | 9.08 | full-sim | 0.000 |  |
| s2-e4a-flower-128mb | E4 | real | [run](https://wandb.ai/hpml_surf-surf/AgnosticFL-stage2/runs/ipft46le) | 3 | - | - | - | 13.51 | full-sim | 0.000 |  |
| s2-e4a-flower-256mb | E4 | real | [run](https://wandb.ai/hpml_surf-surf/AgnosticFL-stage2/runs/dxbgmzsm) | 3 | - | - | - | 20.61 | full-sim | 0.000 |  |
| s2-e4a-vantage6-1mb | E4 | real | [run](https://wandb.ai/hpml_surf-surf/AgnosticFL-stage2/runs/un9as3dt) | 3 | - | - | - | 6.30 | full-sim | 0.000 |  |
| s2-e4a-vantage6-8mb | E4 | real | [run](https://wandb.ai/hpml_surf-surf/AgnosticFL-stage2/runs/4g8x8z7e) | 3 | - | - | - | 12.80 | full-sim | 0.000 |  |
| s2-e4a-vantage6-32mb | E4 | real | [run](https://wandb.ai/hpml_surf-surf/AgnosticFL-stage2/runs/rzpzd5yg) | 3 | - | - | - | 60.12 | full-sim | 0.000 |  |
| s2-e4a-vantage6-64mb | E4 | real | [run](https://wandb.ai/hpml_surf-surf/AgnosticFL-stage2/runs/objmtfr6) | 3 | - | - | - | 115.26 | full-sim | 0.000 |  |
| s2-e4a-vantage6-128mb | E4 | real | [run](https://wandb.ai/hpml_surf-surf/AgnosticFL-stage2/runs/218ysfhw) | 3 | - | - | - | 468.22 | full-sim | 0.000 |  |
| s2-e4a-mixed-1mb | E4 | real | [run](https://wandb.ai/hpml_surf-surf/AgnosticFL-stage2/runs/yynkkqdd) | 3 | - | - | - | 6.46 | full-sim | 0.000 |  |
| s2-e4a-mixed-8mb | E4 | real | [run](https://wandb.ai/hpml_surf-surf/AgnosticFL-stage2/runs/rfof40zg) | 3 | - | - | - | 13.31 | full-sim | 0.000 |  |
| s2-e4a-mixed-32mb | E4 | real | [run](https://wandb.ai/hpml_surf-surf/AgnosticFL-stage2/runs/ubt4yw0z) | 3 | - | - | - | 58.89 | full-sim | 0.000 |  |
| s2-e4a-mixed-64mb | E4 | real | [run](https://wandb.ai/hpml_surf-surf/AgnosticFL-stage2/runs/yxt03omg) | 3 | - | - | - | 116.20 | full-sim | 0.000 |  |

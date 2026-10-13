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
| s2-e1-centralized | E1 | reference | [run](https://wandb.ai/hpml_surf-surf/AgnosticFL-stage2/runs/xqqn9rvo) | 50 | 0.8469 | 0.8296 | 0.9814 | 4.23 | reference | - |  |
| s2-e1-local-iid | E1 | reference | [run](https://wandb.ai/hpml_surf-surf/AgnosticFL-stage2/runs/cea36vjb) | 50 | 0.6919 | - | - | - | reference | - |  |
| s2-e1-local-dir0.1 | E1 | reference | [run](https://wandb.ai/hpml_surf-surf/AgnosticFL-stage2/runs/i0okl2x2) | 50 | 0.2625 | - | - | - | reference | - |  |
| s2-e1-iid-fedavg | E1 | real | [run](https://wandb.ai/hpml_surf-surf/AgnosticFL-stage2/runs/6gumqo95) | 50 | 0.8339 | 0.8219 | 0.9809 | 9.11 | full-sim | 0.000 |  |
| s2-e1-iid-fedavg-seed2027 | E1 | sim | [run](https://wandb.ai/hpml_surf-surf/AgnosticFL-stage2/runs/b88a5xus) | 50 | 0.8403 | 0.8303 | 0.9831 | 2.23 | sim-counterpart:s2-e1-iid-fedavg | 0.000 |  |
| s2-e1-iid-fedavg-seed2028 | E1 | sim | [run](https://wandb.ai/hpml_surf-surf/AgnosticFL-stage2/runs/vj7lcq04) | 50 | 0.8406 | 0.8316 | 0.9813 | 2.28 | sim-counterpart:s2-e1-iid-fedavg | 0.000 |  |
| s2-e1-dir1.0-fedavg | E1 | real | [run](https://wandb.ai/hpml_surf-surf/AgnosticFL-stage2/runs/l8l2dszs) | 50 | 0.7991 | 0.7914 | 0.9745 | 8.87 | full-sim | 0.000 |  |
| s2-e1-dir1.0-fedavg-seed2027 | E1 | sim | [run](https://wandb.ai/hpml_surf-surf/AgnosticFL-stage2/runs/9z89aj38) | 50 | 0.7130 | 0.6959 | 0.9553 | 2.29 | sim-counterpart:s2-e1-dir1.0-fedavg | 0.000 |  |
| s2-e1-dir1.0-fedavg-seed2028 | E1 | sim | [run](https://wandb.ai/hpml_surf-surf/AgnosticFL-stage2/runs/tzkzgd1r) | 50 | 0.7891 | 0.7839 | 0.9713 | 2.22 | sim-counterpart:s2-e1-dir1.0-fedavg | 0.000 |  |
| s2-e1-dir0.3-fedavg | E1 | real | [run](https://wandb.ai/hpml_surf-surf/AgnosticFL-stage2/runs/ckfroyrx) | 50 | 0.7698 | 0.7625 | 0.9651 | 9.13 | full-sim | 0.000 |  |
| s2-e1-dir0.3-fedavg-seed2027 | E1 | sim | [run](https://wandb.ai/hpml_surf-surf/AgnosticFL-stage2/runs/atcji97a) | 50 | 0.7820 | 0.7785 | 0.9682 | 2.23 | sim-counterpart:s2-e1-dir0.3-fedavg | 0.000 |  |
| s2-e1-dir0.3-fedavg-seed2028 | E1 | sim | [run](https://wandb.ai/hpml_surf-surf/AgnosticFL-stage2/runs/c9giods2) | 50 | 0.7872 | 0.7815 | 0.9708 | 2.21 | sim-counterpart:s2-e1-dir0.3-fedavg | 0.000 |  |
| s2-e1-dir0.1-fedavg | E1 | real | [run](https://wandb.ai/hpml_surf-surf/AgnosticFL-stage2/runs/yveo4x3x) | 50 | 0.7342 | 0.7158 | 0.9581 | 9.39 | full-sim | 0.000 |  |
| s2-e1-dir0.1-fedavg-seed2027 | E1 | sim | [run](https://wandb.ai/hpml_surf-surf/AgnosticFL-stage2/runs/ikz84h23) | 50 | 0.7706 | 0.7621 | 0.9669 | 2.24 | sim-counterpart:s2-e1-dir0.1-fedavg | 0.000 |  |
| s2-e1-dir0.1-fedavg-seed2028 | E1 | sim | [run](https://wandb.ai/hpml_surf-surf/AgnosticFL-stage2/runs/fut8oolf) | 50 | 0.7376 | 0.7226 | 0.9565 | 2.28 | sim-counterpart:s2-e1-dir0.1-fedavg | 0.000 |  |
| s2-e1-qty-fedavg | E1 | real | [run](https://wandb.ai/hpml_surf-surf/AgnosticFL-stage2/runs/j3l4n2iv) | 50 | 0.8404 | 0.8270 | 0.9818 | 9.88 | full-sim | 0.000 |  |
| s2-e1-qty-fedavg-seed2027 | E1 | sim | [run](https://wandb.ai/hpml_surf-surf/AgnosticFL-stage2/runs/yfi29x2j) | 50 | 0.8531 | 0.8400 | 0.9850 | 2.20 | sim-counterpart:s2-e1-qty-fedavg | 0.000 |  |

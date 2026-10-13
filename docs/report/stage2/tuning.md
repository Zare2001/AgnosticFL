# Stage 2 — S3 tuning grid

Sim only (`hub.sim`), OrganAMNIST `dir0.3`, 6 base sites, `cnn-small`, 30 rounds, seed 2026, 1 local epoch, batch 64, momentum 0. Selection by **final validation accuracy** (round 30, official validation split, 6,491 images); the client lr is tuned with `fedavg` first and reused by every aggregator. Produced by `scripts/stage2/tune.py`; runs in `~/flhub-runtime/runs/s3/` (not uploaded).

| run | aggregator | value | final val acc | val bal acc | val macro AUC | mean val acc last 3 | best val acc |
|---|---|---|---|---|---|---|---|
| s3-fedavg-lr0.01 | fedavg | lr0.01 | 0.6614 | 0.5253 | 0.9455 | 0.6570 | 0.6732 |
| s3-fedavg-lr0.03 | fedavg | lr0.03 | 0.7786 | 0.7201 | 0.9735 | 0.7689 | 0.7786 |
| s3-fedavg-lr0.05 | fedavg | lr0.05 | 0.8424 | 0.8395 | 0.9846 | 0.8351 | 0.8424 |
| s3-fedavg-lr0.1 | fedavg | lr0.1 | 0.9037 | 0.9123 | 0.9927 | 0.8987 | 0.9037 |
| s3-fedadagrad-eta0.01 | fedadagrad | eta0.01 | 0.7190 | 0.6280 | 0.9776 | 0.7272 | 0.7384 |
| s3-fedadagrad-eta0.03 | fedadagrad | eta0.03 | 0.8848 | 0.8851 | 0.9929 | 0.8839 | 0.9096 |
| s3-fedadagrad-eta0.1 | fedadagrad | eta0.1 | 0.6132 | 0.5464 | 0.9538 | 0.6382 | 0.6601 |
| s3-fedadam-eta0.001 | fedadam | eta0.001 | 0.3292 | 0.2268 | 0.8616 | 0.3266 | 0.3292 |
| s3-fedadam-eta0.003 | fedadam | eta0.003 | 0.6672 | 0.5669 | 0.9479 | 0.6573 | 0.6672 |
| s3-fedadam-eta0.01 | fedadam | eta0.01 | 0.8552 | 0.8337 | 0.9844 | 0.8429 | 0.8552 |
| s3-fedadam-eta0.03 | fedadam | eta0.03 | 0.9059 | 0.8958 | 0.9942 | 0.9040 | 0.9059 |
| s3-fedavgm-eta0.1 | fedavgm | eta0.1 | 0.8188 | 0.8224 | 0.9791 | 0.8146 | 0.8188 |
| s3-fedavgm-eta0.3 | fedavgm | eta0.3 | 0.8926 | 0.8901 | 0.9914 | 0.8756 | 0.8926 |
| s3-fedavgm-eta1 | fedavgm | eta1 | 0.9137 | 0.9169 | 0.9940 | 0.9095 | 0.9137 |
| s3-fedprox-mu0.001 | fedprox | mu0.001 | 0.9019 | 0.9083 | 0.9921 | 0.9000 | 0.9019 |
| s3-fedprox-mu0.01 | fedprox | mu0.01 | 0.9029 | 0.9163 | 0.9937 | 0.9016 | 0.9073 |
| s3-fedprox-mu0.1 | fedprox | mu0.1 | 0.8889 | 0.8937 | 0.9903 | 0.8822 | 0.8889 |
| s3-fedyogi-eta0.001 | fedyogi | eta0.001 | 0.3280 | 0.2245 | 0.8590 | 0.3265 | 0.3280 |
| s3-fedyogi-eta0.003 | fedyogi | eta0.003 | 0.6774 | 0.5849 | 0.9496 | 0.6626 | 0.6774 |
| s3-fedyogi-eta0.01 | fedyogi | eta0.01 | 0.8495 | 0.8198 | 0.9850 | 0.8374 | 0.8495 |
| s3-fedyogi-eta0.03 | fedyogi | eta0.03 | 0.8789 | 0.8765 | 0.9922 | 0.8804 | 0.8877 |

## Chosen values (frozen in `configs/stage2/tuned.yaml`)

- client lr: **0.1** (fedavg final val acc 0.9037)
- fedavgm: **eta = 1** (final val acc 0.9137)
- fedadam: **eta = 0.03** (final val acc 0.9059)
- fedyogi: **eta = 0.03** (final val acc 0.8789)
- fedadagrad: **eta = 0.03** (final val acc 0.8848)
- fedprox: **mu = 0.01** (final val acc 0.9029)

**A\* = fedavgm** (best final validation accuracy among the five; used by E3 and E6).

## Notes

- Four optima lie on the edge of the plan's fixed grid: client lr 0.1 (largest), fedavgm η 1.0 (largest), fedadam η 0.03 and fedyogi η 0.03 (largest). Larger values may do better; the grid was kept as planned (EXPERIMENTS.md §S3) and this is listed as a limitation in the report.
- With β = 0.9, fedavgm η = 1.0 is an effective server step of η/(1−β) = 10 on the pseudo-gradient.
- Seed 2026 only; differences of about 1 pp between the best settings (fedavgm 0.914, fedadam 0.906, fedavg 0.904, fedprox 0.903) are within what seeds may change, so the A* choice is by this single run, as the plan prescribes.

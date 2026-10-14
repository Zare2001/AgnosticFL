# E4a — Flower hospital VM memory per payload (stage 2)

Source: the per-run summaries of `scripts/sysmon.py` (1 s samples) on the Flower hospital VM, `~/flhub-runtime/sysmon/summaries/sysmon-flower-s2-e4a-flower-<S>mb.json`, each covering the run window from the run's start to its end. Command: the Python snippet at the end of this file, run in that directory.

Peak memory on the Flower hospital VM (two SuperNodes, `flw-h5` and `flw-h6`) during the single-framework E4a runs `s2-e4a-flower-<S>mb` (echo model `payload-<S>`, S MiB per site and direction, 3 rounds) on the reference testbed. Units: MiB (2^20 bytes). `mem used` is the VM's used memory; the process columns are the peak resident set size (RSS) of each site's `flwr-clientapp`, `flower-supernode` and `flower-superexec` processes. The VM has 4 vCPUs and 32,097 MiB of RAM. Interface bytes are on `ens3`, the VM's only interface besides loopback (`rx` = received, `tx` = sent). The last column is the sha256 prefix of the summary file.

| S (MiB) | window (UTC) | samples | mem used peak | mem used mean | CPU mean / peak (%) | ClientApp h5 / h6 | SuperNode h5 / h6 | SuperExec h5 / h6 | rx bytes | tx bytes | summary sha256 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 2026-10-13 16:07:55–16:08:14 | 19 | 1923.0 | 1696.0 | 25.1 / 53.1 | 318.8 / 318.8 | 198.6 / 314.2 | 123.5 / 123.4 | 6,421,657 | 5,386,757 | `bf669d3f69ef` |
| 8 | 2026-10-13 16:08:55–16:09:16 | 21 | 2032.5 | 1745.3 | 26.0 / 57.3 | 367.5 / 312.9 | 253.6 / 345.6 | 123.5 / 123.4 | 50,556,680 | 50,576,663 | `1079800da0ca` |
| 32 | 2026-10-13 16:09:56–16:10:22 | 26 | 2156.3 | 1790.0 | 23.7 / 59.4 | 404.5 / 405.2 | 322.1 / 369.8 | 123.5 / 123.4 | 201,844,826 | 201,905,599 | `092cc0e54983` |
| 64 | 2026-10-13 16:10:56–16:11:28 | 32 | 2518.9 | 1960.2 | 21.9 / 60.7 | 635.6 / 624.0 | 404.1 / 406.2 | 123.5 / 123.4 | 403,533,415 | 403,667,273 | `ea8f8db6d651` |
| 128 | 2026-10-13 16:12:02–16:12:49 | 47 | 3704.0 | 2254.3 | 18.5 / 59.7 | 949.7 / 949.2 | 583.6 / 620.1 | 123.5 / 123.4 | 806,850,949 | 807,177,454 | `abad4d8dd807` |
| 256 | 2026-10-13 16:13:22–16:14:41 | 79 | 5053.6 | 2623.6 | 15.5 / 69.9 | 1462.0 / 1433.1 | 920.6 / 981.6 | 123.5 / 123.4 | 1,613,528,780 | 1,614,318,348 | `b0fca8bca342` |
| 512 | 2026-10-14 07:10:58–07:13:48 | 170 | 7723.1 | 3005.4 | 11.9 / 64.8 | 2461.4 / 2461.3 | 1112.6 / 1179.8 | 123.2 / 123.5 | 3,226,973,675 | 3,231,437,628 | `27c26bcd65e8` |

At 512 MiB the VM's used memory peaked at 7,723 MiB (7.54 GiB, 24 % of RAM); each ClientApp peaked at about 4.8 × the payload and each SuperNode at 2.2–2.3 ×. The SuperExec processes stay at about 124 MiB at every size.

```python
import json, hashlib
from pathlib import Path
for s in [1, 8, 32, 64, 128, 256, 512]:
    f = Path(f"sysmon-flower-s2-e4a-flower-{s}mb.json")
    d = json.loads(f.read_text())
    p, sy, net = d["procs"], d["system"], d["net"][d["main_iface"]]
    pk = lambda k: p[k]["rss_mb_peak"]
    print(s, d["from"], d["to"], d["samples"], sy["mem_used_mb_peak"], sy["mem_used_mb_mean"], sy["cpu_mean"], sy["cpu_peak"],
          pk("flw-h5-clientapp"), pk("flw-h6-clientapp"), pk("flw-h5-supernode"), pk("flw-h6-supernode"),
          pk("flw-h5-superexec"), pk("flw-h6-superexec"), net["rx_bytes"], net["tx_bytes"],
          hashlib.sha256(f.read_bytes()).hexdigest()[:12])
```

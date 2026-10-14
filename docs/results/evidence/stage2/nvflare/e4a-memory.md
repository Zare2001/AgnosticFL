# NVFlare E4a memory on the NVFlare hospital VM (final runs)

Source: `~/flhub-runtime/sysmon/summaries/s2-e4a-nvflare-<N>mb-nvflare-sysmon.json` on the NVFlare hospital VM (`scripts/sysmon.py summarize` over each run window, started by `deploy/nvflare-vm/sysmon.sh send`); table printed by the command at the end of this file.

sysmon samples once per second. Its "MB" are MiB (`/proc/meminfo` KiB / 1024; RSS pages × page size / 2^20); GiB = MiB / 1024. *VM* = memory in use on the whole VM: two NVFlare clients and the operating system (MemTotal 31.3 GiB). *Job* = peak RSS of one client's job process, the larger of the two sites. *Client* = peak RSS of the NVFlare client process (`client_train`). Payload `payload-<N>` = N MiB per site and direction.

| payload (MiB) | run | VM peak (MiB) | VM peak (GiB) | job RSS peak (MiB) | job RSS peak (GiB) | client RSS peak (MiB) |
|---|---|---|---|---|---|---|
| 1 | s2-e4a-nvflare-1mb | 1721.5 | 1.68 | 439.1 | 0.43 | 88.8 |
| 8 | s2-e4a-nvflare-8mb | 1782.4 | 1.74 | 484.2 | 0.47 | 88.8 |
| 32 | s2-e4a-nvflare-32mb | 2019.5 | 1.97 | 596.6 | 0.58 | 88.8 |
| 64 | s2-e4a-nvflare-64mb | 2254.9 | 2.20 | 736.6 | 0.72 | 88.8 |
| 128 | s2-e4a-nvflare-128mb | 2716.8 | 2.65 | 969.4 | 0.95 | 88.8 |
| 256 | s2-e4a-nvflare-256mb | 3559.8 | 3.48 | 1384.4 | 1.35 | 88.8 |
| 512 | s2-e4a-nvflare-512mb | 5129.7 | 5.01 | 2163.6 | 2.11 | 96.0 |

Command (on the NVFlare hospital VM):

```bash
python3 - <<'EOF'
import json
import sys
from pathlib import Path

d = Path(sys.argv[1] if len(sys.argv) > 1 else Path.home() / "flhub-runtime/sysmon/summaries")
print("| payload (MiB) | run | VM peak (MiB) | VM peak (GiB) | job RSS peak (MiB) | job RSS peak (GiB) | client RSS peak (MiB) |")
print("|---|---|---|---|---|---|---|")
for mb in (1, 8, 32, 64, 128, 256, 512):
    run = f"s2-e4a-nvflare-{mb}mb"
    s = json.loads((d / f"{run}-nvflare-sysmon.json").read_text())
    vm = s["system"]["mem_used_mb_peak"]
    job = max(s["procs"][f"{x}-job"]["rss_mb_peak"] for x in ("nvf-h1", "nvf-h2"))
    cl = max(s["procs"][f"{x}-client"]["rss_mb_peak"] for x in ("nvf-h1", "nvf-h2"))
    print(f"| {mb} | {run} | {vm:.1f} | {vm / 1024:.2f} | {job:.1f} | {job / 1024:.2f} | {cl:.1f} |")
EOF
```

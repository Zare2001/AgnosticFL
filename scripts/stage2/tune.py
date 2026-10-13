"""S3 tuning grid (EXPERIMENTS.md §S3): sim only, validation split, dir0.3, 30 rounds, seed 2026.

    python scripts/stage2/tune.py [--parallel 3] [--nice 10]

1. client lr with fedavg -> lr* (best final validation accuracy);
2. writes lr* to configs/stage2/tuned.yaml, regenerates the configs, runs the 17 aggregator settings;
3. best value per aggregator; A* = best of fedavgm, fedadam, fedyogi, fedadagrad, fedprox;
4. writes configs/stage2/tuned.yaml (tuned: true) and docs/report/stage2/tuning.md.
Runs land in ~/flhub-runtime/runs/s3/ and are not uploaded.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
CONF = REPO / "configs" / "stage2"
RUNS = Path.home() / "flhub-runtime" / "runs" / "s3"
PY = sys.executable
AGGS = ["fedavgm", "fedadam", "fedyogi", "fedadagrad", "fedprox"]


def run_sim(name: str, nice: int) -> dict:
    cfg = CONF / "s3" / f"{name}.yaml"
    log = RUNS / f"{name}.log"
    RUNS.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    with open(log, "w") as f:
        rc = subprocess.run(["nice", "-n", str(nice), PY, "-m", "hub.sim", "--config", str(cfg), "--name", name],
                            cwd=REPO, stdout=f, stderr=subprocess.STDOUT).returncode
    summary = RUNS / f"{name}-latest" / "summary.json"
    out = json.loads(summary.read_text()) if rc == 0 and summary.exists() else {"status": f"failed rc={rc}"}
    metrics = [json.loads(x) for x in (RUNS / f"{name}-latest" / "metrics.jsonl").read_text().splitlines()] \
        if rc == 0 else []
    accs = [m["global/val_acc"] for m in metrics if "global/val_acc" in m]
    out.update({"name": name, "seconds": round(time.time() - t0), "val_acc_last3": sum(accs[-3:]) / 3 if accs else None,
                "val_acc_best": max(accs) if accs else None})
    print(f"{name}: final val acc {out.get('final/val_acc')} ({out['seconds']} s)", flush=True)
    return out


def regenerate() -> None:
    subprocess.run([PY, "scripts/stage2/make_configs.py"], cwd=REPO, check=True)


def write_tuned(values: dict) -> None:
    path = CONF / "tuned.yaml"
    head = "# S3 tuning result (scripts/stage2/tune.py); read by scripts/stage2/make_configs.py\n"
    path.write_text(head + yaml.safe_dump(values, sort_keys=False))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--parallel", type=int, default=3)
    ap.add_argument("--nice", type=int, default=10)
    args = ap.parse_args(argv)
    regenerate()
    lr_names = sorted(p.stem for p in (CONF / "s3").glob("s3-fedavg-lr*.yaml"))
    with ThreadPoolExecutor(args.parallel) as pool:
        lr_res = list(pool.map(lambda n: run_sim(n, args.nice), lr_names))
    best_lr = max(lr_res, key=lambda r: (r.get("final/val_acc") or -1))
    lr_star = float(best_lr["name"].split("-lr")[1])
    write_tuned({"lr": lr_star, "tuned": False})
    regenerate()
    agg_names = sorted(p.stem for p in (CONF / "s3").glob("s3-*.yaml") if not p.stem.startswith("s3-fedavg-lr"))
    with ThreadPoolExecutor(args.parallel) as pool:
        agg_res = list(pool.map(lambda n: run_sim(n, args.nice), agg_names))
    best = {}
    for a in AGGS:
        rows = [r for r in agg_res if r["name"].startswith(f"s3-{a}-")]
        b = max(rows, key=lambda r: (r.get("final/val_acc") or -1))
        best[a] = (float(b["name"].rsplit("-", 1)[1].lstrip("etamu")), b.get("final/val_acc"))
    a_star = max(AGGS, key=lambda a: best[a][1] or -1)
    tuned = {"lr": lr_star, **{a: best[a][0] for a in AGGS}, "a_star": a_star, "tuned": True}
    write_tuned(tuned)
    regenerate()
    lines = ["# Stage 2 — S3 tuning grid", "",
             "Sim only (`hub.sim`), OrganAMNIST `dir0.3`, 6 base sites, `cnn-small`, 30 rounds, seed 2026, "
             "1 local epoch, batch 64, momentum 0. Selection by **final validation accuracy** (round 30, official "
             "validation split, 6,491 images); the client lr is tuned with `fedavg` first and reused by every "
             "aggregator. Produced by `scripts/stage2/tune.py`; runs in `~/flhub-runtime/runs/s3/` (not uploaded).", "",
             "| run | aggregator | value | final val acc | val bal acc | val macro AUC | mean val acc last 3 | best val acc |",
             "|---|---|---|---|---|---|---|---|"]
    for r in lr_res + agg_res:
        parts = r["name"].split("-")
        lines.append(f"| {r['name']} | {parts[1]} | {parts[2]} | {r.get('final/val_acc', 0):.4f} | "
                     f"{r.get('final/val_bal_acc', 0):.4f} | {r.get('final/val_auc', 0):.4f} | "
                     f"{(r.get('val_acc_last3') or 0):.4f} | {(r.get('val_acc_best') or 0):.4f} |")
    lines += ["", "## Chosen values (frozen in `configs/stage2/tuned.yaml`)", "",
              f"- client lr: **{lr_star:g}** (fedavg final val acc {best_lr.get('final/val_acc'):.4f})"]
    for a in AGGS:
        lines.append(f"- {a}: **{'mu' if a == 'fedprox' else 'eta'} = {best[a][0]:g}** (final val acc {best[a][1]:.4f})")
    lines += ["", f"**A\\* = {a_star}** (best final validation accuracy among the five; used by E3 and E6)."]
    (REPO / "docs" / "report" / "stage2" / "tuning.md").write_text("\n".join(lines) + "\n")
    print(json.dumps(tuned))
    return 0


if __name__ == "__main__":
    sys.exit(main())

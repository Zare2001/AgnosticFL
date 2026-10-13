"""Report figures for REPORT_STAGE2.md (docs/agents/stage2/REPORT_TEMPLATE.md, figure list).

    python scripts/stage2/plots.py [--runs ~/flhub-runtime/runs/stage2] [--out docs/report/stage2/figures]
                                   [--only e1_acc ...]

Reads the run dirs (metrics.jsonl, summary.json, rounds/*.json, sysmon/*.json); every figure is
skipped (with a note) while its runs are missing. PNG <= 300 KB each. Colours: the dataviz
reference palette; frameworks always nvflare = blue, vantage6 = orange, flower = aqua.
"""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

RUNS = Path.home() / "flhub-runtime" / "runs" / "stage2"
OUT = Path(__file__).resolve().parents[2] / "docs" / "report" / "stage2" / "figures"
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
FW_COLOR = {"nvflare": SERIES[0], "vantage6": SERIES[1], "flower": SERIES[2], "mixed": SERIES[6]}
SURFACE, TEXT, TEXT2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"
PIDS = ["iid", "dir1.0", "dir0.3", "dir0.1", "qty", "fwcorr", "mix"]
AGGS = ["fedavg", "fedavgm", "fedadam", "fedyogi", "fedadagrad", "fedprox"]
SEEDS = [2026, 2027, 2028]


def style() -> None:
    plt.rcParams.update({"font.size": 9, "text.color": TEXT, "axes.labelcolor": TEXT2, "axes.edgecolor": GRID,
                         "xtick.color": TEXT2, "ytick.color": TEXT2, "axes.grid": True, "grid.color": GRID,
                         "grid.linewidth": 0.6, "axes.spines.top": False, "axes.spines.right": False,
                         "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "legend.frameon": False,
                         "lines.linewidth": 2.0})


class Run:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.metrics = [json.loads(x) for x in (path / "metrics.jsonl").read_text().splitlines() if x.strip()] \
            if (path / "metrics.jsonl").exists() else []
        self.summary = json.loads((path / "summary.json").read_text()) if (path / "summary.json").exists() else {}
        self.config = json.loads((path / "config.json").read_text()) if (path / "config.json").exists() else {}

    def series(self, key: str) -> tuple[list[int], list[float]]:
        pts = [(m["step"], m[key]) for m in self.metrics if key in m]
        return [p[0] for p in pts], [p[1] for p in pts]

    def records(self) -> list[dict]:
        return [json.loads(p.read_text()) for p in sorted((self.path / "rounds").glob("r[0-9][0-9][0-9][0-9].json"))]


def load(name: str, root: Path = RUNS) -> Run | None:
    p = root / f"{name}-latest"
    return Run(p.resolve()) if p.exists() else None


def save(fig, name: str, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{name}.png"
    fig.savefig(path, dpi=110, bbox_inches="tight", facecolor=SURFACE)
    plt.close(fig)
    print(f"{path} {path.stat().st_size / 1024:.0f} KB")


def final_acc(run: Run | None, key: str = "global/test_acc") -> float | None:
    if run is None:
        return None
    vals = [m[key] for m in run.metrics if key in m]
    return vals[-1] if vals else None


# ---- E1 / E2 learning ----------------------------------------------------------------------------
def fig_e1_acc(out: Path) -> None:
    runs = {pid: load(f"s2-e1-{pid}-fedavg") for pid in PIDS}
    if not any(runs.values()):
        print("e1_acc: no E1 runs yet")
        return
    fig, ax = plt.subplots(figsize=(7.5, 4.2))
    for i, pid in enumerate(PIDS):
        if runs[pid]:
            x, y = runs[pid].series("global/test_acc")
            ax.plot(x, y, color=SERIES[i], label=pid)
    cen = load("s2-e1-centralized")
    if cen:
        ax.axhline(final_acc(cen), color=TEXT2, lw=1, ls="--", label="centralized (final)")
    ax.set_xlabel("round")
    ax.set_ylabel("test accuracy")
    ax.set_title("E1: FedAvg test accuracy per partition (real runs, seed 2026)", loc="left")
    ax.legend(ncol=4, loc="lower right", fontsize=8)
    save(fig, "e1_accuracy_vs_round", out)


def seed_values(name: str, key: str) -> list[float]:
    vals = []
    for seed in SEEDS:
        run = load(name if seed == 2026 else f"{name}-seed{seed}")
        v = final_acc(run, key)
        if v is not None:
            vals.append(v)
    return vals


def fig_e1_final(out: Path) -> None:
    rows = [(pid, seed_values(f"s2-e1-{pid}-fedavg", "global/test_acc"),
             seed_values(f"s2-e1-{pid}-fedavg", "siteshaped/min_acc")) for pid in PIDS]
    rows = [r for r in rows if r[1]]
    if not rows:
        print("e1_final: no E1 runs yet")
        return
    fig, ax = plt.subplots(figsize=(7.5, 3.8))
    x = np.arange(len(rows))
    for off, idx, label, color in ((-0.18, 1, "test accuracy", SERIES[0]), (0.18, 2, "worst-site (site-shaped)",
                                                                             SERIES[1])):
        means = [np.mean(r[idx]) if r[idx] else np.nan for r in rows]
        sds = [np.std(r[idx]) if len(r[idx]) > 1 else 0 for r in rows]
        ax.bar(x + off, means, width=0.34, color=color, label=label, yerr=sds, capsize=3, ecolor=TEXT2)
    ax.set_xticks(x, [r[0] for r in rows])
    ax.set_ylabel("final accuracy (mean ± sd over seeds)")
    ax.set_title("E1: final accuracy and worst-site accuracy per partition (FedAvg)", loc="left")
    ax.legend(loc="lower left", fontsize=8)
    save(fig, "e1_final_and_worst_site", out)


def fig_e2_grid(out: Path) -> None:
    pids = ["iid", "dir0.3", "dir0.1"]
    data = {}
    for pid in pids:
        for agg in AGGS:
            name = f"s2-e1-{pid}-fedavg" if agg == "fedavg" else f"s2-e2-{pid}-{agg}"
            data[(pid, agg)] = seed_values(name, "global/test_acc")
    if not any(data.values()):
        print("e2_grid: no E2 runs yet")
        return
    fig, ax = plt.subplots(figsize=(8.5, 4.0))
    width = 0.13
    x = np.arange(len(pids))
    for i, agg in enumerate(AGGS):
        means = [np.mean(data[(p, agg)]) if data[(p, agg)] else np.nan for p in pids]
        sds = [np.std(data[(p, agg)]) if len(data[(p, agg)]) > 1 else 0 for p in pids]
        ax.bar(x + (i - 2.5) * width, means, width=width * 0.92, color=SERIES[i], label=agg, yerr=sds, capsize=2,
               ecolor=TEXT2)
    ax.set_xticks(x, pids)
    ax.set_ylabel("final test accuracy (mean ± sd, 3 seeds)")
    ax.set_title("E2: aggregator × partition (seed 2026 real, 2027/2028 hub.sim)", loc="left")
    ax.legend(ncol=3, fontsize=8, loc="lower left")
    save(fig, "e2_final_accuracy_grid", out)


def fig_e2_dir01(out: Path) -> None:
    runs = {agg: load("s2-e1-dir0.1-fedavg" if agg == "fedavg" else f"s2-e2-dir0.1-{agg}") for agg in AGGS}
    if not any(runs.values()):
        print("e2_dir01: no runs yet")
        return
    fig, ax = plt.subplots(figsize=(7.5, 4.0))
    for i, agg in enumerate(AGGS):
        if runs[agg]:
            x, y = runs[agg].series("global/test_acc")
            ax.plot(x, y, color=SERIES[i], label=agg)
    ax.set_xlabel("round")
    ax.set_ylabel("test accuracy")
    ax.set_title("E2: accuracy vs round on dir0.1 per aggregator (real, seed 2026)", loc="left")
    ax.legend(ncol=3, fontsize=8, loc="lower right")
    save(fig, "e2_dir01_accuracy_vs_round", out)


FIGURES = {"e1_acc": fig_e1_acc, "e1_final": fig_e1_final, "e2_grid": fig_e2_grid, "e2_dir01": fig_e2_dir01}


def main(argv: list[str] | None = None) -> int:
    global RUNS
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", default=str(RUNS))
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--only", nargs="*")
    args = ap.parse_args(argv)
    RUNS = Path(args.runs).expanduser()
    style()
    for key, fn in FIGURES.items():
        if not args.only or key in args.only:
            fn(Path(args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

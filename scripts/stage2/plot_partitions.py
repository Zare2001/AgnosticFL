"""Site x class heat maps of the stage-2 partitions (report figure 1).

    python scripts/stage2/plot_partitions.py [--manifests configs/stage2/partitions]
                                             [--out docs/results/figures/partitions.png]

One panel per OrganAMNIST pid; cell = share of the site's samples in that class (row-normalised),
row label = site and its size, so both label skew and quantity skew are visible.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap  # noqa: E402

# dataviz reference palette: sequential blue ramp 100..700, light surface and text tokens
BLUE_RAMP = ["#fcfcfb", "#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]
SURFACE, TEXT, TEXT2 = "#fcfcfb", "#0b0b0b", "#52514e"
PIDS = ["iid", "dir1.0", "dir0.3", "dir0.1", "qty", "fwcorr", "mix", "iid12"]
LABELS = {"iid": "stratified, equal sizes", "dir1.0": "label skew, Dir(1.0 p)", "dir0.3": "label skew, Dir(0.3 p)",
          "dir0.1": "label skew, Dir(0.1 p)", "qty": "quantity skew 1:32:2:16:4:8", "fwcorr": "framework-correlated",
          "mix": "dir0.3 labels + qty sizes", "iid12": "stratified, 12 sites"}
SHORT = {"femur-left": "femur-L", "femur-right": "femur-R", "kidney-left": "kidney-L", "kidney-right": "kidney-R",
         "lung-left": "lung-L", "lung-right": "lung-R"}


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifests", default="configs/stage2/partitions")
    ap.add_argument("--dataset", default="organamnist")
    ap.add_argument("--out", default="docs/results/figures/partitions.png")
    args = ap.parse_args(argv)
    cmap = LinearSegmentedColormap.from_list("blue", BLUE_RAMP)
    plt.rcParams.update({"font.size": 8, "text.color": TEXT, "axes.labelcolor": TEXT2,
                         "xtick.color": TEXT2, "ytick.color": TEXT2, "axes.edgecolor": SURFACE})
    fig, axes = plt.subplots(2, 4, figsize=(15, 7.6), facecolor=SURFACE, layout="constrained",
                             gridspec_kw={"height_ratios": [1, 1.45]})
    image = None
    for ax, pid in zip(axes.flat, PIDS):
        m = json.loads((Path(args.manifests) / f"{args.dataset}-{pid}.json").read_text())
        sites = list(m["sites"])
        hist = np.array([m["sites"][s]["label_hist"] for s in sites], dtype=float)
        share = hist / hist.sum(axis=1, keepdims=True)
        image = ax.imshow(share, cmap=cmap, vmin=0, vmax=1, aspect="auto", interpolation="nearest")
        ax.set_title(f"{pid}: {LABELS[pid]}", fontsize=8.5, color=TEXT, loc="left")
        ax.set_yticks(range(len(sites)), [f"{s}  n={m['sites'][s]['n']}" for s in sites], fontsize=7)
        ax.set_xticks(range(m["num_classes"]), [SHORT.get(c, c) for c in m["class_names"]], rotation=60,
                      ha="right", fontsize=6.5)
        ax.tick_params(length=0)
        ax.set_facecolor(SURFACE)
    cbar = fig.colorbar(image, ax=axes, shrink=0.5)
    cbar.set_label("share of the site's training samples", color=TEXT2)
    cbar.outline.set_visible(False)
    fig.suptitle(f"Stage-2 partitions of the {args.dataset} training split (partition_seed 2026); "
                 "cell = share of the site's samples in that class", x=0.01, ha="left", fontsize=11, color=TEXT)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=100, facecolor=SURFACE, bbox_inches="tight")
    print(f"{out} {out.stat().st_size / 1024:.0f} KB")


if __name__ == "__main__":
    main()

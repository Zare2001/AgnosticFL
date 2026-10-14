"""Shared look of the documentation figures (scripts/docs/plots_*.py).

One colour per framework, the same in every figure: NVFlare blue, vantage6 orange, Flower aqua
(the first three slots of a palette whose three-colour set stays distinguishable under the common
colour-vision deficiencies). Runs that are not one framework (mixed, simulation, centralized) use
neutral inks with their own line style, so they never look like a fourth framework.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
FIGURES = REPO / "docs" / "results" / "figures"

FRAMEWORK = {"nvflare": "#2a78d6", "vantage6": "#eb6834", "flower": "#1baf7a"}
LABEL = {"nvflare": "NVFlare", "vantage6": "vantage6", "flower": "Flower"}
INK = "#0b0b0b"        # primary text, the mixed run
INK_2 = "#52514e"      # secondary text, the simulation
MUTED = "#898781"      # axis labels, the centralized reference
GRID = "#e1e0d9"
AXIS = "#c3c2b7"
SURFACE = "#fcfcfb"


def setup() -> None:
    plt.rcParams.update({
        "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
        "axes.edgecolor": AXIS, "axes.linewidth": 0.8, "axes.labelcolor": INK_2, "axes.titlecolor": INK,
        "axes.titlesize": 11, "axes.titleweight": "bold", "axes.labelsize": 9.5, "axes.titlelocation": "left",
        "axes.spines.top": False, "axes.spines.right": False, "axes.grid": True, "axes.axisbelow": True,
        "grid.color": GRID, "grid.linewidth": 0.8, "grid.linestyle": "-",
        "xtick.color": MUTED, "ytick.color": MUTED, "xtick.labelcolor": INK_2, "ytick.labelcolor": INK_2,
        "xtick.labelsize": 8.5, "ytick.labelsize": 8.5,
        "legend.frameon": False, "legend.fontsize": 8.5, "legend.labelcolor": INK_2,
        "lines.linewidth": 2.0, "lines.solid_capstyle": "round", "lines.solid_joinstyle": "round",
        "lines.markersize": 5.5, "font.size": 9.5, "figure.dpi": 100, "savefig.dpi": 150,
    })


def save(fig, name: str, out_dir: Path = FIGURES, note: str | None = None) -> Path:
    """Write docs/results/figures/<name>.png (only under docs/); `note` is a small source line."""
    out_dir = Path(out_dir).resolve()
    if REPO / "docs" not in out_dir.parents and out_dir != REPO / "docs":
        raise SystemExit(f"refusing to write outside docs/: {out_dir}")
    if note:
        fig.text(0.01, 0.01, note, fontsize=7.5, color=MUTED, ha="left", va="bottom")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{name}.png"
    fig.savefig(path, bbox_inches="tight", pad_inches=0.15)
    plt.close(fig)
    print(f"wrote {path.relative_to(REPO)} ({path.stat().st_size // 1024} KB)")
    return path

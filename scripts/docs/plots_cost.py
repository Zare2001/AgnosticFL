"""Cost and efficiency figures for docs/cost-and-efficiency.md (docs tooling; writes only under docs/).

    python scripts/docs/plots_cost.py

Reads only generated evidence in the repository, never run directories or W&B:

    cost_bytes_on_wire.png   bytes on the wire per byte of model update, per framework and payload
                             (docs/results/evidence/stage2/e4_bytes.md)
    cost_model_fit.png       fan-out time per round against payload, with the fitted a + b*P line
                             (docs/results/evidence/stage2/tables.md E4a, cost_model.md)
    cost_loc.png             lines of code per component (scripts/docs/loc.py, counted now)
"""

from __future__ import annotations

import re
import sys

import figstyle as fs
import loc

EVIDENCE = fs.REPO / "docs" / "results" / "evidence" / "stage2"
FW = ("nvflare", "vantage6", "flower")


def md_table(path, first_header: str) -> list[dict[str, str]]:
    """Rows of the first Markdown table whose header row starts with `| first_header |`."""
    lines = path.read_text().splitlines()
    for i, line in enumerate(lines):
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if line.startswith("|") and cells and cells[0] == first_header:
            rows = []
            for row in lines[i + 2:]:
                if not row.startswith("|"):
                    break
                vals = [c.strip() for c in row.strip().strip("|").split("|")]
                rows.append(dict(zip(cells, vals, strict=False)))
            return rows
    raise SystemExit(f"no table starting with {first_header!r} in {path}")


def fig_bytes_on_wire() -> None:
    rows = md_table(EVIDENCE / "e4_bytes.md", "run")
    fig, ax = fs.plt.subplots(figsize=(9, 4.2))
    for fw in FW:
        pts = []
        for r in rows:
            m = re.fullmatch(rf"s2-e4a-{fw}-(\d+)mb", r["run"])
            if m and r["framework"] == fw:
                pts.append((int(m.group(1)), float(r["ratio"])))
        pts.sort()
        # rows the framework operators marked as counter artefacts (ratio < 1 is impossible for a payload that
        # provably crossed the link): NVFlare 8 MiB (tx short of the payload), Flower 1 MiB (count too coarse)
        odd = {(8, "nvflare"), (1, "flower")}
        good = [(p, v) for p, v in pts if (p, fw) not in odd]
        bad = [(p, v) for p, v in pts if (p, fw) in odd]
        col = fs.FRAMEWORK[fw]
        ax.plot([p for p, _ in good], [v for _, v in good], marker="o", color=col, label=fs.LABEL[fw])
        if bad:
            ax.scatter([p for p, _ in bad], [v for _, v in bad], marker="o", s=34, facecolors="none",
                       edgecolors=col, linewidths=1.5, zorder=3)
    ax.axhline(1.0, color=fs.INK_2, lw=1, ls=(0, (4, 2)))
    ax.text(640, 1.04, "1.0 = every update\ncrosses the link once", fontsize=8, color=fs.INK_2, va="bottom")
    ax.axhline(5.34, color=fs.FRAMEWORK["vantage6"], lw=1, ls=(0, (1, 1.5)))
    ax.text(640, 5.1, "5.34 = vantage6 by design\n(5 input copies + 1 result,\nx1.78 encoding)", fontsize=8,
            color=fs.INK_2, va="top")
    ax.set_xscale("log", base=2)
    ax.set_xticks([1, 8, 32, 64, 128, 256, 512], ["1", "8", "32", "64", "128", "256", "512"])
    ax.set_yscale("log")
    ax.set_yticks([0.8, 1, 2, 4, 6, 10], ["0.8", "1", "2", "4", "6", "10"])
    ax.set_ylim(0.75, 12)
    ax.set_xlim(0.8, 2400)
    ax.set_xlabel("model update size (MiB, payload echo model)")
    ax.set_ylabel("bytes on the wire / payload")
    ax.set_title("Network efficiency: interface bytes per byte of model update (stage 2, E4a, 2 sites)")
    ax.legend(loc="upper left")
    fig.tight_layout()
    fs.save(fig, "cost_bytes_on_wire",
            note="Open markers: counter artefacts (NVFlare 8 MiB, Flower 1 MiB). vantage6 256 MiB: result never delivered. "
                 "Source: docs/results/evidence/stage2/e4_bytes.md")


def _fanout(rows: list[dict[str, str]], fw: str) -> list[tuple[int, float]]:
    pts = []
    for r in rows:
        if r["scope"] == fw and r["status"] == "finished":
            m = re.search(rf"{fw} ([0-9.]+)", r["per-framework fan-out s"])
            pts.append((int(r["MiB"]), float(m.group(1))))
    return sorted(pts)


def fig_cost_model() -> None:
    e4a = md_table(EVIDENCE / "tables.md", "scope")
    fits = {r["framework"]: r for r in md_table(EVIDENCE / "cost_model.md", "framework")}
    use = {"nvflare": fits["nvflare"], "flower": fits["flower"], "vantage6": fits["vantage6 (1-64 MiB, used below)"]}
    fig, axes = fs.plt.subplots(1, 3, figsize=(12, 3.9))
    for ax, fw in zip(axes, ("nvflare", "flower", "vantage6"), strict=True):
        pts = _fanout(e4a, fw)
        a, b, r2 = float(use[fw]["a (s)"]), float(use[fw]["b (s/MiB)"]), use[fw]["R²"]
        sizes = [int(x) for x in use[fw]["sizes (MiB)"].split(",")]
        col = fs.FRAMEWORK[fw]
        in_fit = [(p, t) for p, t in pts if p in sizes]
        out_fit = [(p, t) for p, t in pts if p not in sizes]
        ax.scatter([p for p, _ in in_fit], [t for _, t in in_fit], color=col, s=34, zorder=3, label="measured (in fit)")
        if out_fit:
            ax.scatter([p for p, _ in out_fit], [t for _, t in out_fit], s=34, facecolors="none", edgecolors=col,
                       linewidths=1.5, zorder=3, label="measured (not in fit)")
        xmax = max(p for p, _ in pts)
        ax.plot([0, xmax], [a, a + b * xmax], color=fs.INK_2, lw=1.4, ls=(0, (4, 2)),
                label=f"fit {a:.2f} s + {b:.4g} s/MiB x P")
        ax.set_title(f"{fs.LABEL[fw]}  (R² {r2}, fit on {min(sizes)}-{max(sizes)} MiB)", fontsize=10)
        ax.set_xlabel("payload P (MiB)")
        ax.set_xlim(0, xmax * 1.05)
        ax.set_ylim(0, None)
        ax.legend(loc="upper left", fontsize=7.5)
    axes[0].set_ylabel("fan-out time per round (s)")
    if any(r["scope"] == "vantage6" and r["status"] == "failed" for r in e4a):
        axes[2].text(0.98, 0.04, "256 MiB: result never delivered\n(framework limit)", transform=axes[2].transAxes,
                     ha="right", fontsize=8, color=fs.INK_2)
    fig.suptitle("Cost model per framework: fixed cost a plus cost per MiB b (stage 2, E4a echo sweeps, median of "
                 "rounds 2-3)", x=0.01, ha="left", fontsize=11, fontweight="bold", color=fs.INK)
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    fs.save(fig, "cost_model_fit",
            note="Each panel has its own y scale: vantage6's per-MiB cost is about 70x NVFlare's. Source: "
                 "docs/results/evidence/stage2/cost_model.md, tables.md (E4a)")


GROUP_LABEL = {"hub": "hub", "adapter": "adapters", "library": "shared library", "client": "hospital clients",
               "baseline": "E7 baselines", "tests": "tests", "deploy": "deployment", "scripts": "scripts"}


def _color(component: str) -> str:
    for fw in FW:
        if fs.LABEL[fw].lower() in component.lower():
            return fs.FRAMEWORK[fw]
    return fs.INK_2 if not component.startswith(("mock", "native", "scripts")) else fs.MUTED


def fig_loc() -> None:
    rows = loc.count()
    fig, ax = fs.plt.subplots(figsize=(10, 7.2))
    ys, labels, y = [], [], 0
    for r in reversed(rows):
        ax.barh(y, r["code_lines"], height=0.66, color=_color(r["component"]), edgecolor=fs.SURFACE, linewidth=2)
        ax.text(r["code_lines"] + 25, y, f"{r['code_lines']:,}", va="center", fontsize=8, color=fs.INK_2)
        ys.append(y)
        labels.append(f"{r['component']}  [{GROUP_LABEL[r['group']]}]")
        y += 1
    ax.set_yticks(ys, labels, fontsize=8)
    ax.set_xlabel("code lines (non-blank, non-comment)")
    ax.grid(axis="y", visible=False)
    ax.set_xlim(0, max(r["code_lines"] for r in rows) * 1.12)
    ax.set_title("Implementation size per component")
    fig.tight_layout()
    fs.save(fig, "cost_loc", note="Colour = framework-specific component. Counted by scripts/docs/loc.py; table: "
                                   "docs/results/evidence/loc.md")


def main() -> int:
    fs.setup()
    fig_bytes_on_wire()
    fig_cost_model()
    fig_loc()
    return 0


if __name__ == "__main__":
    sys.exit(main())

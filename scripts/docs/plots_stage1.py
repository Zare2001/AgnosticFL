"""Stage-1 figures for the documentation (docs tooling; writes only under docs/).

    python scripts/docs/plots_stage1.py extract [--runs ~/flhub-runtime/runs]
    python scripts/docs/plots_stage1.py plot

`extract` reads the run directories of the six stage-1 final runs (found by the W&B run id in
their summary.json; these are the runs uploaded to the W&B project AgnosticFL) and writes their
per-round metrics to docs/results/evidence/stage1/final-runs.json. `plot` needs only that file
and the stage-1 equivalence evidence in the same folder, so the figures can be rebuilt from the
repository alone:

    stage1_accuracy_loss.png       test accuracy and loss per round, all six final runs
    stage1_round_time.png          seconds per round, per run (framework-only runs, mixed, references)
    stage1_equivalence.png         hub vs framework-free simulation: max weight difference per round
    stage1_final_metrics.png       final test accuracy and AUC, federated vs centralized
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

import figstyle as fs

EVIDENCE = fs.REPO / "docs" / "results" / "evidence" / "stage1"
RUNS_JSON = EVIDENCE / "final-runs.json"
WANDB_PROJECT = "hpml_surf-surf/AgnosticFL"

# the six stage-1 final runs (W&B project AgnosticFL, tag final, group final-2026-10-13)
FINAL_RUNS = [
    ("mixed-6sites", "iehjnc44", "mixed (NVFlare + vantage6 + Flower, 6 sites)"),
    ("nvflare-only", "pavr8q6u", "NVFlare only (2 sites)"),
    ("vantage6-only", "twk49dhp", "vantage6 only (2 sites)"),
    ("flower-only", "011lasv0", "Flower only (2 sites)"),
    ("sim-fedavg-reference", "kur37gs2", "framework-free simulation (6 sites)"),
    ("centralized-baseline", "j5gn5knm", "centralized (pooled data)"),
]
STYLE = {
    "mixed-6sites": dict(color=fs.INK, ls="-", lw=2.0),
    "nvflare-only": dict(color=fs.FRAMEWORK["nvflare"], ls="-", lw=2.0),
    "vantage6-only": dict(color=fs.FRAMEWORK["vantage6"], ls="-", lw=2.0),
    "flower-only": dict(color=fs.FRAMEWORK["flower"], ls="-", lw=2.0),
    "sim-fedavg-reference": dict(color=fs.INK_2, ls=(0, (3, 2)), lw=1.6),
    "centralized-baseline": dict(color=fs.MUTED, ls=(0, (1, 1.6)), lw=2.0),
}
EQUIVALENCE = [
    ("equivalence-final.json", "mixed-6sites vs simulation, 30 rounds (final run)"),
    ("equivalence-mixed-p2.json", "mixed, 3 rounds (integration test)"),
    ("equivalence-nvflare-island.json", "NVFlare island, 3 rounds"),
    ("equivalence-vantage6-island.json", "vantage6 island, 3 rounds"),
    ("equivalence-flower-island.json", "Flower island, 3 rounds"),
]
TOLERANCE = 1e-5


# ---- extract ------------------------------------------------------------------------------------
def find_run_dir(runs: Path, wandb_id: str) -> Path:
    for summary in sorted(runs.glob("*/summary.json")):
        if summary.parent.is_symlink():
            continue
        if json.loads(summary.read_text()).get("wandb_id") == wandb_id:
            return summary.parent
    raise SystemExit(f"no run dir with wandb_id {wandb_id} under {runs}")


def extract(runs: Path) -> None:
    out = {"source": "stage-1 run directories (metrics.jsonl, summary.json) of the final runs uploaded to "
                     f"the W&B project {WANDB_PROJECT}; extracted by scripts/docs/plots_stage1.py extract",
           "metrics": "per step: test accuracy, loss and AUC on the PneumoniaMNIST test split (624 images) after "
                      "that round (step 0 = initial model; centralized: epochs); duration_s = round wall time",
           "runs": []}
    for name, wid, label in FINAL_RUNS:
        rd = find_run_dir(runs.expanduser(), wid)
        summary = json.loads((rd / "summary.json").read_text())
        rows = []
        for line in (rd / "metrics.jsonl").read_text().splitlines():
            m = json.loads(line)
            rows.append({"step": m["step"], "test_acc": m.get("global/test_acc"), "test_loss": m.get("global/test_loss"),
                         "test_auc": m.get("global/test_auc"), "duration_s": m.get("round/duration_s"),
                         "num_sites": m.get("round/num_sites")})
        out["runs"].append({"name": name, "label": label, "wandb_id": wid, "wandb_url": summary.get("wandb_url"),
                            "run_dir": rd.name, "status": summary.get("status"), "rounds": summary.get("rounds"),
                            "duration_s": summary.get("duration_s"), "final_global_ref": summary.get("final/global_ref"),
                            "final": {k.split("/", 1)[1]: v for k, v in summary.items() if k.startswith("final/")
                                      and k != "final/global_ref"},
                            "per_round": rows})
        print(f"{name:22s} {wid}  {rd.name}  {len(rows)} steps")
    RUNS_JSON.write_text(dumps_compact(out))
    print(f"wrote {RUNS_JSON.relative_to(fs.REPO)}")


def dumps_compact(out: dict) -> str:
    """JSON with one line per run header and one line per round (diff-friendly, about 200 lines)."""
    lines = ["{"]
    lines += [f" {json.dumps(k)}: {json.dumps(v)}," for k, v in out.items() if k != "runs"]
    lines.append(' "runs": [')
    for i, run in enumerate(out["runs"]):
        head = {k: v for k, v in run.items() if k != "per_round"}
        lines.append("  " + json.dumps(head)[:-1] + ', "per_round": [')
        rows = [f"   {json.dumps(r)}" for r in run["per_round"]]
        lines.append(",\n".join(rows))
        lines.append("  ]}" + ("," if i < len(out["runs"]) - 1 else ""))
    lines += [" ]", "}"]
    text = "\n".join(lines) + "\n"
    assert json.loads(text) == out
    return text


# ---- plot ---------------------------------------------------------------------------------------
def load_runs() -> list[dict]:
    return json.loads(RUNS_JSON.read_text())["runs"]


def fig_accuracy_loss(runs: list[dict]) -> None:
    fig, axes = fs.plt.subplots(1, 2, figsize=(11, 4.2))
    for run in runs:
        steps = [r["step"] for r in run["per_round"]]
        st = STYLE[run["name"]]
        for ax, key in zip(axes, ("test_acc", "test_loss"), strict=True):
            ax.plot(steps, [r[key] for r in run["per_round"]], label=run["label"], **st)
    axes[0].set_title("Test accuracy per round")
    axes[0].set_ylabel("test accuracy")
    axes[0].axhline(0.625, color=fs.AXIS, lw=1, zorder=0)
    axes[0].text(30.4, 0.625, "majority class\n(62.5 %)", fontsize=7.5, color=fs.MUTED, va="center")
    axes[1].set_title("Test loss per round")
    axes[1].set_ylabel("test loss (cross-entropy)")
    for ax in axes:
        ax.set_xlabel("round (centralized: epoch)")
        ax.set_xlim(0, 30)
    axes[0].set_ylim(0.45, 0.92)
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=3, bbox_to_anchor=(0.5, -0.12))
    fig.suptitle("Stage 1: PneumoniaMNIST, CNN (51,618 parameters), FedAvg, 30 rounds", x=0.01, ha="left",
                 fontsize=11, fontweight="bold", color=fs.INK)
    fig.tight_layout(rect=(0, 0.02, 1, 0.97))
    fs.save(fig, "stage1_accuracy_loss",
            note="The simulation line lies exactly on the mixed line (bit-identical weights). Source: "
                 "docs/results/evidence/stage1/final-runs.json")


def fig_round_time(runs: list[dict]) -> None:
    order = ["nvflare-only", "vantage6-only", "flower-only", "mixed-6sites", "sim-fedavg-reference",
             "centralized-baseline"]
    by_name = {r["name"]: r for r in runs}
    fig, ax = fs.plt.subplots(figsize=(9, 3.8))
    for y, name in enumerate(reversed(order)):
        run = by_name[name]
        d = [r["duration_s"] for r in run["per_round"] if r["step"] >= 1 and r["duration_s"] is not None]
        color = STYLE[name]["color"]
        jitter = [((i * 7) % 11 - 5) * 0.045 for i in range(len(d))]  # deterministic, to show repeated values
        ax.scatter(d, [y + j for j in jitter], s=18, color=color, alpha=0.6, linewidths=0, zorder=3)
        med, mean = statistics.median(d), statistics.fmean(d)
        ax.plot([med, med], [y - 0.36, y + 0.36], color=color, lw=2.5, zorder=4)
        ax.text(max(d) + 0.25, y, f"median {med:.1f} s, mean {mean:.1f} s", va="center", fontsize=8.5,
                color=fs.INK_2)
    ax.set_yticks(range(len(order)), [by_name[n]["label"] for n in reversed(order)])
    ax.set_xlabel("wall time per round (s)")
    ax.set_xlim(0, 12.5)
    ax.grid(axis="y", visible=False)
    ax.set_title("Stage 1: seconds per round (rounds 1-30, one dot per round)")
    fig.tight_layout()
    fs.save(fig, "stage1_round_time",
            note="Round time includes the hub's global evaluation. Times are multiples of about 1 s because the "
                 "stage-1 orchestrator polled the adapters every second. Source: docs/results/evidence/stage1/final-runs.json")


def fig_equivalence(runs: list[dict]) -> None:
    fig, axes = fs.plt.subplots(1, 2, figsize=(11, 4), gridspec_kw={"width_ratios": [1.15, 1]})
    ax = axes[0]
    markers = ["o", "s", "^", "D", "v"]
    colors = [fs.INK, fs.INK_2, fs.FRAMEWORK["nvflare"], fs.FRAMEWORK["vantage6"], fs.FRAMEWORK["flower"]]
    worst = 0.0
    for (fname, label), mk, col in zip(EQUIVALENCE, markers, colors, strict=True):
        data = json.loads((EVIDENCE / fname).read_text())
        per_round = data.get("per_round") or data["rounds"]  # the vantage6 island file names it "rounds"
        xs = [r["round"] for r in per_round]
        ys = [r["max_abs_diff"] for r in per_round]
        worst = max(worst, max(ys))
        ax.scatter(xs, ys, marker=mk, s=34 if mk == "o" else 26, color=col, label=label, zorder=3,
                   edgecolors=fs.SURFACE, linewidths=0.8)
    ax.set_yscale("symlog", linthresh=1e-8)
    ax.set_ylim(-1e-9, 1e-3)
    ax.axhline(TOLERANCE, color=fs.INK_2, ls=(0, (4, 2)), lw=1.2)
    ax.text(0.3, TOLERANCE * 1.6, "pass threshold 1e-5", fontsize=8, color=fs.INK_2)
    ax.set_xlabel("round (0 = initial model)")
    ax.set_ylabel("max |w_hub - w_sim| over all tensors")
    ax.set_xlim(-0.8, 30.8)
    ax.set_title(f"Global weights, hub vs simulation (largest difference: {worst:.1f})")
    ax.legend(loc="upper right", fontsize=7.5)
    ax2 = axes[1]
    by_name = {r["name"]: r for r in runs}
    for name in ("mixed-6sites", "sim-fedavg-reference"):
        run = by_name[name]
        st = dict(STYLE[name])
        if name == "sim-fedavg-reference":
            st.update(marker="o", ms=4, lw=0, mfc="none")
        ax2.plot([r["step"] for r in run["per_round"]], [r["test_acc"] for r in run["per_round"]],
                 label=run["label"], **st)
    ax2.set_title("Test accuracy per round, the same two runs")
    ax2.set_xlabel("round")
    ax2.set_ylabel("test accuracy")
    ax2.set_xlim(0, 30)
    ax2.legend(loc="upper left", fontsize=7.5)
    fig.tight_layout()
    fs.save(fig, "stage1_equivalence",
            note="Equivalence check (scripts/equivalence_check.py): every checkpoint identical; the final models have the "
                 "same sha256. Source: docs/results/evidence/stage1/equivalence-*.json")


def fig_final_metrics(runs: list[dict]) -> None:
    order = ["centralized-baseline", "sim-fedavg-reference", "mixed-6sites", "nvflare-only", "vantage6-only",
             "flower-only"]
    by_name = {r["name"]: r for r in runs}
    fig, axes = fs.plt.subplots(1, 2, figsize=(11, 3.6), sharey=True)
    for ax, key, title in zip(axes, ("test_acc", "test_auc"), ("Final test accuracy", "Final test AUC"), strict=True):
        ys = list(range(len(order)))[::-1]
        for y, name in zip(ys, order, strict=True):
            v = by_name[name]["final"][key]
            ax.barh(y, v, height=0.6, color=STYLE[name]["color"], edgecolor=fs.SURFACE, linewidth=2)
            ax.text(v + 0.01, y, f"{v:.3f}", va="center", fontsize=8.5, color=fs.INK_2)
        ax.set_xlim(0, 1.08)
        ax.set_title(title)
        ax.grid(axis="y", visible=False)
    axes[0].set_yticks(list(range(len(order)))[::-1], [by_name[n]["label"] for n in order])
    axes[0].axvline(0.625, color=fs.AXIS, lw=1, zorder=0)
    fig.suptitle("Stage 1: final metrics after 30 rounds (centralized: 30 epochs), test split of 624 images",
                 x=0.01, ha="left", fontsize=11, fontweight="bold", color=fs.INK)
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    fs.save(fig, "stage1_final_metrics",
            note="Grey line in the left panel: majority-class accuracy 0.625. Source: "
                 "docs/results/evidence/stage1/final-runs.json")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="scripts/docs/plots_stage1.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("extract", help="copy the final runs' per-round metrics into the evidence folder")
    e.add_argument("--runs", default="~/flhub-runtime/runs", help="stage-1 runs directory")
    sub.add_parser("plot", help="write the stage-1 figures from the evidence folder")
    args = ap.parse_args(argv)
    if args.cmd == "extract":
        extract(Path(args.runs))
        return 0
    fs.setup()
    runs = load_runs()
    fig_accuracy_loss(runs)
    fig_round_time(runs)
    fig_equivalence(runs)
    fig_final_metrics(runs)
    return 0


if __name__ == "__main__":
    sys.exit(main())

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
import sys
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
        sds = [np.std(r[idx], ddof=1) if len(r[idx]) > 1 else 0 for r in rows]
        ax.bar(x + off, means, width=0.34, color=color, label=label, yerr=sds, capsize=3, ecolor=TEXT2)
    ax.set_xticks(x, [r[0] for r in rows])
    ax.set_ylabel("final accuracy (mean ± sd over seeds)")
    ax.set_title("E1: final accuracy and worst-site accuracy per partition (FedAvg)", loc="left")
    ax.legend(ncol=2, fontsize=8, loc="upper center", bbox_to_anchor=(0.5, -0.1), frameon=False)
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
        sds = [np.std(data[(p, agg)], ddof=1) if len(data[(p, agg)]) > 1 else 0 for p in pids]
        ax.bar(x + (i - 2.5) * width, means, width=width * 0.92, color=SERIES[i], label=agg, yerr=sds, capsize=2,
               ecolor=TEXT2)
    ax.set_xticks(x, pids)
    ax.set_ylabel("final test accuracy (mean ± sd, 3 seeds)")
    ax.set_title("E2: aggregator × partition (seed 2026 real, 2027/2028 hub.sim)", loc="left")
    ax.legend(ncol=6, fontsize=8, loc="upper center", bbox_to_anchor=(0.5, -0.1), frameon=False)
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


# ---- performance ------------------------------------------------------------------------------------
FRAMEWORKS = ["nvflare", "vantage6", "flower"]
PREFIX = {"nvf": "nvflare", "v6": "vantage6", "flw": "flower"}


def fw_of(site: str) -> str:
    return PREFIX.get(site.split("-")[0], "?")


def med(values: list[float]) -> float | None:
    values = [v for v in values if v is not None]
    return statistics.median(values) if values else None


def rounds_from(run: Run, first: int = 2) -> list[dict]:
    return [m for m in run.metrics if m.get("step", 0) >= first and "round/duration_s" in m]


def site_keys(row: dict, suffix: str) -> dict[str, float]:
    return {k.split("/")[1]: v for k, v in row.items() if k.startswith("site/") and k.endswith("/" + suffix)}


def breakdown(run: Run) -> dict[str, dict[str, float]]:
    """Per framework: median over rounds >= 2 of mean train time, mean overhead, plus hub agg/eval."""
    out = {}
    rows = rounds_from(run)
    for fw in FRAMEWORKS:
        train = [statistics.mean([v for s, v in site_keys(r, "train_time_s").items() if fw_of(s) == fw] or [np.nan])
                 for r in rows]
        over = [statistics.mean([v for s, v in site_keys(r, "overhead_s").items() if fw_of(s) == fw] or [np.nan])
                for r in rows]
        if any(np.isfinite(train)):
            out[fw] = {"train": float(np.nanmedian(train)), "overhead": float(np.nanmedian(over)),
                       "agg": med([r["round/agg_s"] for r in rows]), "eval": med([r["round/eval_s"] for r in rows])}
    return out


def fig_e3_breakdown(out: Path) -> None:
    a_star = "fedavgm"
    names = [("cnn-small", "s2-e1-dir0.3-fedavg"), ("cnn-medium", "s2-e3-cnn-medium-fedavg"),
             ("resnet18-gn", "s2-e3-resnet18-gn-fedavg")]
    data = [(model, breakdown(r)) for model, n in names if (r := load(n))]
    if len(data) < 2:
        print("e3_breakdown: E3 runs missing")
        return
    fig, axes = plt.subplots(1, len(data), figsize=(4 * len(data), 3.8), sharey=False)
    axes = np.atleast_1d(axes)
    parts = [("train", SERIES[0], "local training"), ("overhead", SERIES[1], "transport + framework + adapter"),
             ("agg", SERIES[2], "hub aggregation"), ("eval", SERIES[3], "hub evaluation")]
    for ax, (model, bd) in zip(axes, data):
        x = np.arange(len(bd))
        bottom = np.zeros(len(bd))
        for key, color, label in parts:
            vals = np.array([bd[fw][key] or 0 for fw in bd])
            ax.bar(x, vals, bottom=bottom, color=color, width=0.6, label=label, edgecolor=SURFACE, linewidth=1)
            bottom += vals
        ax.set_xticks(x, list(bd))
        ax.set_title(model, loc="left")
        ax.set_ylabel("seconds per round (median, rounds >= 2)")
    axes[0].legend(fontsize=7, loc="upper left")
    fig.suptitle(f"E3: round-time breakdown per framework and model (fedavg; A* = {a_star} in the run table)",
                 x=0.01, ha="left")
    save(fig, "e3_round_time_breakdown", out)


def e4a_points(scope: str) -> list[tuple[float, float | None, Run | None]]:
    pts = []
    for size in [1, 8, 32, 64, 128, 256, 512, 1024]:
        run = load(f"s2-e4a-{scope}-{size}mb")
        if run is None:
            continue
        ok = run.summary.get("status") == "finished"
        t = med([m["round/duration_s"] for m in rounds_from(run)]) if ok else None
        pts.append((size, t, run))
    return pts


def e4a_limits(scope: str) -> list[tuple[int, str]]:
    """(size, result) of the sizes the sweep stopped at (testbed disk limit, failed)."""
    p = RUNS / f"e4a-{scope}.json"
    if not p.exists():
        return []
    return [(r["size_mb"], r["result"]) for r in json.loads(p.read_text())["runs"] if r["result"] not in ("ok",)]


def fig_e4a_time(out: Path) -> None:
    scopes = ["nvflare", "vantage6", "flower", "mixed"]
    data = {s: e4a_points(s) for s in scopes}
    if not any(data.values()):
        print("e4a_time: no E4a runs yet")
        return
    fig, ax = plt.subplots(figsize=(7.4, 4.6))
    for scope in scopes:
        good = [(x, t) for x, t, _ in data[scope] if t is not None]
        if not good:
            continue
        ax.plot([g[0] for g in good], [g[1] for g in good], "o-", color=FW_COLOR[scope], ms=5)
        ax.annotate(scope, good[-1], xytext=(-5, 8), textcoords="offset points", ha="right", va="bottom",
                    fontsize=8, color=TEXT)
        for size, result in e4a_limits(scope):
            ax.plot([size], [good[-1][1]], marker="v" if "disk" in result else "x", color=FW_COLOR[scope],
                    ms=8, mfc="none", mew=1.5)
    ax.set_xscale("log", base=2)
    ax.set_yscale("log")
    ax.set_xticks([1, 8, 32, 64, 128, 256, 512], ["1", "8", "32", "64", "128", "256", "512"])
    ax.set_xlabel("payload per site and direction (MiB)")
    ax.set_ylabel("round time, median of rounds 2-3 (s)")
    ax.set_title("E4a: round time vs payload (open triangle = next size stopped by the testbed disk rule)",
                 loc="left", fontsize=9)
    save(fig, "e4a_round_time_vs_payload", out)


def sysmon_peak(run: Run, component_prefix: str) -> float | None:
    peaks = []
    for f in (run.path / "sysmon").glob("*.json"):
        d = json.loads(f.read_text())
        for name, p in (d.get("procs") or {}).items():
            if name.startswith(component_prefix):
                peaks.append(p["rss_mb_peak"])
    return max(peaks) if peaks else None


def fig_e4a_rss(out: Path) -> None:
    comps = [("hub-core", "hub core"), ("adapter-", "adapters"), ("nvflare-server", "NVFlare server"),
             ("v6-server", "vantage6 server"), ("flower-superlink", "Flower SuperLink")]
    pts = {c: [] for c, _ in comps}
    for size in [1, 8, 32, 64, 128, 256, 512]:
        run = load(f"s2-e4a-mixed-{size}mb")
        if run is None:
            continue
        for c, _ in comps:
            v = sysmon_peak(run, c)
            if v is not None:
                pts[c].append((size, v))
    if not any(pts.values()):
        print("e4a_rss: no sysmon data yet")
        return
    fig, ax = plt.subplots(figsize=(7, 4.2))
    for i, (c, label) in enumerate(comps):
        if pts[c]:
            ax.plot([p[0] for p in pts[c]], [p[1] for p in pts[c]], "o-", color=SERIES[i], label=label, ms=4)
    ax.set_xscale("log", base=2)
    sizes = sorted({x for line in ax.get_lines() for x in line.get_xdata()})
    ax.set_xticks(sizes, [f"{x:g}" for x in sizes])
    ax.set_yscale("log")
    ax.set_xlabel("payload (MiB)")
    ax.set_ylabel("peak RSS on central (MB)")
    ax.set_title("E4a (mixed scope): peak memory of the central components", loc="left")
    ax.legend(fontsize=8, loc="center left", bbox_to_anchor=(1.01, 0.5), frameon=False)
    save(fig, "e4a_peak_rss_vs_payload", out)


RATES = {"wan1g": 1000.0, "wan100": 100.0, "wan20": 20.0}


def fig_e4b(out: Path) -> None:
    runs = {p: load(f"s2-e4b-{p}") for p in ["none", "wan1g", "wan100", "wan20"]}
    if not any(runs.values()):
        print("e4b: no runs yet")
        return
    payload_mbit = 45 * 2**20 * 8 / 1e6
    fig, ax = plt.subplots(figsize=(7.5, 4.2))
    width = 0.25
    profiles = [p for p in runs if runs[p]]
    x = np.arange(len(profiles))
    for i, fw in enumerate(FRAMEWORKS):
        vals = [med([m.get(f"adapter/{fw}/fanout_s") for m in rounds_from(runs[p])]) for p in profiles]
        ax.bar(x + (i - 1) * width, [v or 0 for v in vals], width=width * 0.9, color=FW_COLOR[fw], label=fw)
        for j, p in enumerate(profiles):
            if p in RATES:
                bound = 4 * payload_mbit / RATES[p] * (4 / 3 if fw == "vantage6" else 1)
                ax.plot([x[j] + (i - 1) * width - width * 0.45, x[j] + (i - 1) * width + width * 0.45], [bound, bound],
                        color=TEXT, lw=1.5)
    ax.set_xticks(x, profiles)
    ax.set_ylabel("dispatch -> last result per framework (s)")
    ax.set_title("E4b: payload-45 fan-out time vs the bandwidth lower bound (black line)", loc="left")
    ax.legend(fontsize=8)
    save(fig, "e4b_measured_vs_bound", out)


def fig_e5(out: Path) -> None:
    runs = {n: load(f"s2-e5-{n}sites") for n in (6, 12)}
    if not all(runs.values()):
        print("e5: runs missing")
        return
    fig, ax = plt.subplots(figsize=(6.5, 4.0))
    x = np.arange(2)
    width = 0.2
    for i, fw in enumerate(FRAMEWORKS):
        vals = [med([m.get(f"adapter/{fw}/fanout_s") for m in rounds_from(runs[n])]) or 0 for n in (6, 12)]
        ax.bar(x + (i - 1.5) * width, vals, width=width * 0.9, color=FW_COLOR[fw], label=f"{fw} fan-out")
    rt = [med([m["round/duration_s"] for m in rounds_from(runs[n])]) for n in (6, 12)]
    ax.bar(x + 1.5 * width, rt, width=width * 0.9, color=SERIES[6], label="round time")
    ax.set_xticks(x, ["6 sites", "12 sites"])
    ax.set_ylabel("seconds (median, rounds >= 2)")
    ax.set_title("E5: round time and per-framework fan-out, 6 vs 12 sites", loc="left")
    ax.legend(fontsize=8)
    save(fig, "e5_scaling", out)


def fig_e6_timeline(out: Path, name: str = "s2-e6a-strict") -> None:
    from datetime import datetime

    run = load(name)
    if run is None:
        print("e6_timeline: run missing")
        return

    def sec(utc: str) -> float:
        return datetime.fromisoformat(utc.rstrip("Z") + "+00:00").timestamp()

    recs = [r for r in run.records() if r.get("round", 0) > 0]
    t0 = min(sec(r["utc_start"]) for r in recs)
    t_end = max(sec(r["utc_end"]) for r in recs) - t0
    fig, ax = plt.subplots(figsize=(8, 3.8))
    for r in recs:
        a, b = sec(r["utc_start"]) - t0, sec(r["utc_end"]) - t0
        color = SERIES[7] if r.get("failures") else (SERIES[3] if r.get("dropped") else SERIES[0])
        ax.barh(r["round"], b - a, left=a, color=color, height=0.7)
    marks = []  # (begin s, back s, label)
    path = run.path / "injections.jsonl"
    if path.exists():  # central's injections (E6d, E6e)
        evs = [json.loads(x) for x in path.read_text().splitlines()]
        for ev in evs:
            if ev["action"] == "server_restart_begin":
                back = next((x for x in evs if x["action"] == "server_restart_end"
                             and x["framework"] == ev["framework"] and x["round"] == ev["round"]), ev)
                marks.append((sec(ev["utc"]) - t0, sec(back["utc"]) - t0, f"{ev['framework']} restart ({ev['kind']})"))
            elif ev["action"] == "sigkill":
                back = next((x for x in evs if x["action"] == "resume"), ev)
                marks.append((sec(ev["utc"]) - t0, sec(back["utc"]) - t0, "SIGKILL hub + adapters"))
    else:  # the framework agents' injections, announced on the bus
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from e6_table import bus_injections

        for i in bus_injections():
            if -60 <= i["t"] - t0 <= t_end:
                what = i["subject"].split(" at ")[0].replace("E6 inject ", "")
                marks.append((i["t"] - t0, i["t"] - t0, what))
    for a, b, label in marks:
        if b > a:
            ax.axvspan(a, b, color=TEXT2, alpha=0.15, lw=0)
        ax.axvline(a, color=TEXT2, lw=0.8, ls=":")
        top = a > t_end / 2  # the bars run from top left to bottom right: label where they are not
        ax.text(a, 0.97 if top else 0.03, f" {label}", transform=ax.get_xaxis_transform(), rotation=90, fontsize=7,
                va="top" if top else "bottom", ha="right", color=TEXT2)
    ax.invert_yaxis()
    ax.yaxis.set_major_locator(plt.MaxNLocator(integer=True))
    ax.set_xlabel("seconds since the start of round 1 (grey: component down)")
    ax.set_ylabel("round")
    ax.set_title(f"E6: {name} (blue: round; red: failed attempt, then retried; yellow: sites dropped)", loc="left",
                 fontsize=9)
    save(fig, f"e6_timeline_{name}", out)


def fig_e7(out: Path) -> None:
    rows = []
    for fw in FRAMEWORKS:
        for cfg in ("cnn", "payload"):
            hub, nat = load(f"s2-e7-{fw}-hub-{cfg}"), load(f"s2-e7-{fw}-native-{cfg}")
            if hub and nat:
                rows.append((f"{fw}\n{cfg}", med([m["round/duration_s"] for m in rounds_from(hub)]),
                             med([m.get("round/duration_s") for m in rounds_from(nat)])))
    if not rows:
        print("e7: runs missing")
        return
    fig, ax = plt.subplots(figsize=(8, 4.0))
    x = np.arange(len(rows))
    ax.bar(x - 0.18, [r[2] or 0 for r in rows], width=0.34, color=SERIES[2], label="native FedAvg")
    ax.bar(x + 0.18, [r[1] or 0 for r in rows], width=0.34, color=SERIES[0], label="hub path")
    ax.set_xticks(x, [r[0] for r in rows])
    ax.set_ylabel("median round time (s, rounds >= 2)")
    ax.set_title("E7: native FedAvg vs the hub path per framework", loc="left")
    ax.legend(fontsize=8)
    save(fig, "e7_native_vs_hub", out)


FIGURES = {"e1_acc": fig_e1_acc, "e1_final": fig_e1_final, "e2_grid": fig_e2_grid, "e2_dir01": fig_e2_dir01,
           "e3": fig_e3_breakdown, "e4a_time": fig_e4a_time, "e4a_rss": fig_e4a_rss, "e4b": fig_e4b, "e5": fig_e5,
           "e6": fig_e6_timeline, "e7": fig_e7}


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

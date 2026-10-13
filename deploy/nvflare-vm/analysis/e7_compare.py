"""E7: NVFlare native FedAvg vs the hub path, from the two run dirs' metrics.jsonl (+ native_vs_hub.json).

Round time compared like for like: native `round/duration_s` (FedAvg send_model -> aggregated model) vs
hub `round/duration_s + round/agg_s` (dispatch -> envelopes collected -> aggregated); the hub's
evaluation/checkpoint time (`round/total_s`) is reported separately. Medians and IQR over rounds >= 2.

    python3 -I e7_compare.py <native_run_dir> <hub_run_dir>
"""
import json
import statistics as st
import sys
from pathlib import Path


def rows(run: Path) -> dict:
    out = {}
    for line in (run / "metrics.jsonl").read_text().splitlines():
        if line.strip():
            r = json.loads(line)
            out[int(r.get("step", 0))] = r
    return out


def stats(xs):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    q = st.quantiles(xs, n=4) if len(xs) > 1 else [xs[0]] * 3
    return {"median": round(st.median(xs), 3), "iqr": round(q[2] - q[0], 3), "n": len(xs)}


def main():
    nat, hub = Path(sys.argv[1]), Path(sys.argv[2])
    n, h = rows(nat), rows(hub)
    rounds = sorted(r for r in n if r >= 1 and r in h)
    nat_t = {r: n[r].get("round/duration_s") for r in rounds}
    hub_t = {r: (h[r].get("round/duration_s") or 0) + (h[r].get("round/agg_s") or 0) for r in rounds}
    hub_total = {r: h[r].get("round/total_s") for r in rounds}
    diffs = {}
    nvh = nat / "native_vs_hub.json"
    if nvh.exists():
        diffs = {r["round"]: r for r in json.loads(nvh.read_text())["rounds"]}
    print("| round | native s | hub s (dispatch..aggregated) | hub total s (+eval) | global max abs diff | one-step aggregation diff |")
    print("|---|---|---|---|---|---|")
    for r in rounds:
        d = diffs.get(r, {})
        print(f"| {r} | {nat_t[r]} | {round(hub_t[r], 3)} | {hub_total[r] and round(hub_total[r], 3)} | "
              f"{d.get('global_max_abs_diff')} | {d.get('one_step_aggregation_diff')} |")
    late = [r for r in rounds if r >= 2]
    sn, sh = stats([nat_t[r] for r in late]), stats([hub_t[r] for r in late])
    summary = {"native": sn, "hub": sh, "hub_total": stats([hub_total[r] for r in late])}
    if sn and sh:
        summary["overhead_s"] = round(sh["median"] - sn["median"], 3)
        summary["overhead_pct"] = round(100 * (sh["median"] - sn["median"]) / sn["median"], 1)
    if diffs:
        summary["native_vs_hub_max_diff"] = max((v.get("global_max_abs_diff") or 0) for v in diffs.values())
        summary["max_one_step_aggregation_diff"] = max((v.get("one_step_aggregation_diff") or 0) for v in diffs.values())
    print()
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()

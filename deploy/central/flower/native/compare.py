"""Compare a native Flower run with the hub run of the same config (E7 analysis).

    python deploy/central/flower/native/compare.py <native_run_dir> <hub_run_dir> [-o compare.json]

Per round: max |w_native - w_hub| over all tensors (rounds/rNNNN.safetensors; rounds whose
checkpoints were removed by retention are compared by hash only), equality of the global refs and
of every site's update ref, and the round times (round/duration_s = dispatch -> all results).
Timing summary over rounds >= 2 (round 1 is warm-up): median and IQR native vs hub, overhead of
the hub path in seconds and percent. Native Flower FedAvg accumulates p_k * w_k in float32 in reply
order; the hub's fedavg sums in float64 and casts once, so the two agree bit-exactly only when
every p_k * w_k is exact in float32 (e.g. two sites of equal size, p_k = 0.5).
"""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

import numpy as np
from flhub_core.weights import from_safetensors


def rows(run: Path) -> dict[int, dict]:
    out = {}
    for line in (run / "metrics.jsonl").read_text().splitlines():
        if line.strip():
            r = json.loads(line)
            out[r["step"]] = r
    return out


def record(run: Path, rnd: int) -> dict:
    p = run / "rounds" / f"r{rnd:04d}.json"
    return json.loads(p.read_text()) if p.exists() else {}


def max_diff(a: Path, b: Path, rnd: int) -> float | None:
    pa, pb = (r / "rounds" / f"r{rnd:04d}.safetensors" for r in (a, b))
    if not (pa.exists() and pb.exists()):
        return None
    wa, wb = from_safetensors(pa.read_bytes()), from_safetensors(pb.read_bytes())
    if list(wa) != list(wb):
        raise ValueError(f"round {rnd}: tensor names differ")
    return max(float(np.max(np.abs(wa[k].astype(np.float64) - wb[k].astype(np.float64)))) for k in wa)


def quartiles(xs: list[float]) -> tuple[float, float, float] | None:
    if not xs:
        return None
    if len(xs) < 4:
        return min(xs), statistics.median(xs), max(xs)
    q = statistics.quantiles(xs, n=4)
    return q[0], q[1], q[2]


def compare(native: Path, hub: Path) -> dict:
    na, hu = rows(native), rows(hub)
    per_round = []
    for rnd in sorted(set(na) & set(hu)):
        ra, rb = record(native, rnd), record(hub, rnd)
        ua = {u["site_id"]: u.get("weights") for u in ra.get("updates", [])}
        ub = {u["site_id"]: u.get("weights") for u in rb.get("updates", [])}
        per_round.append({
            "round": rnd, "global_ref_equal": na[rnd]["global_ref"] == hu[rnd]["global_ref"],
            "max_abs_diff": max_diff(native, hub, rnd),
            "site_updates_equal": {s: ua[s] == ub.get(s) for s in ua} if rnd else {},
            "native_duration_s": na[rnd].get("round/duration_s"), "hub_duration_s": hu[rnd].get("round/duration_s"),
            "native_test_acc": na[rnd].get("global/test_acc"), "hub_test_acc": hu[rnd].get("global/test_acc"),
        })
    timed = [r for r in per_round if r["round"] >= 2 and r["native_duration_s"] and r["hub_duration_s"]]
    qn = quartiles([r["native_duration_s"] for r in timed])
    qh = quartiles([r["hub_duration_s"] for r in timed])
    diffs = [r["max_abs_diff"] for r in per_round if r["max_abs_diff"] is not None]
    out = {"native": str(native), "hub": str(hub), "rounds": len(per_round) - 1,
           "native_vs_hub_max_diff": max(diffs) if diffs else None,
           "global_refs_equal_all": all(r["global_ref_equal"] for r in per_round),
           "native_round_s": {"q1": qn[0], "median": qn[1], "q3": qn[2]} if qn else None,
           "hub_round_s": {"q1": qh[0], "median": qh[1], "q3": qh[2]} if qh else None,
           "per_round": per_round}
    if qn and qh:
        out["hub_overhead_s"] = qh[1] - qn[1]
        out["hub_overhead_pct"] = 100.0 * (qh[1] - qn[1]) / qn[1]
    flwr = native / "flwr_run.json"
    if flwr.exists():
        try:
            run = json.loads(flwr.read_text())["runs"][0]
            out["native_flower_run"] = {k: run.get(k) for k in ("status", "network-traffic", "compute-time")}
        except (ValueError, KeyError, IndexError):
            pass
    return out


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python deploy/central/flower/native/compare.py")
    ap.add_argument("native")
    ap.add_argument("hub")
    ap.add_argument("-o", "--output")
    args = ap.parse_args(argv)
    res = compare(Path(args.native).expanduser(), Path(args.hub).expanduser())
    for r in res["per_round"]:
        diff = "n/a" if r["max_abs_diff"] is None else f"{r['max_abs_diff']:.3e}"
        print(f"round {r['round']:3d}  refs equal {str(r['global_ref_equal']):5s}  max|diff| {diff:9s}"
              f"  native {r['native_duration_s'] or 0:6.2f} s  hub {r['hub_duration_s'] or 0:6.2f} s")
    print(json.dumps({k: v for k, v in res.items() if k != "per_round"}, indent=1))
    if args.output:
        Path(args.output).write_text(json.dumps(res, indent=1) + "\n")


if __name__ == "__main__":
    main()

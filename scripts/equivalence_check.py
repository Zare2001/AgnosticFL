"""Compare two runs round by round (PROJECT_PLAN.md §6 P3).

    python scripts/equivalence_check.py RUN_A RUN_B [--weight-tol 1e-5] [--acc-tol-pp 0.1]
                                        [--json OUT] [--wandb-run ENTITY/PROJECT/RUN_ID]

RUN_A/RUN_B: run dirs written by hub.orchestrator / hub.sim (e.g. ~/flhub-runtime/runs/mixed-6sites-latest).
PASS when the max abs difference of the global weights is <= --weight-tol after every round
(round 0 = initial model included) and the final test accuracies differ by <= --acc-tol-pp
percentage points. --wandb-run writes the result into that W&B run's summary.
Exit code 0 = PASS, 1 = FAIL.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from flhub_core.weights import from_safetensors, max_abs_diff


def load_run(path: str) -> tuple[Path, dict[int, Path], dict[int, dict]]:
    run = Path(path).expanduser().resolve()
    rounds = {int(p.stem[1:]): p for p in sorted((run / "rounds").glob("r*.safetensors"))}
    metrics = {}
    for line in (run / "metrics.jsonl").read_text().splitlines():
        rec = json.loads(line)
        metrics[rec["step"]] = rec
    return run, rounds, metrics


def compare(path_a: str, path_b: str, weight_tol: float, acc_tol_pp: float) -> dict:
    run_a, rounds_a, metrics_a = load_run(path_a)
    run_b, rounds_b, metrics_b = load_run(path_b)
    if sorted(rounds_a) != sorted(rounds_b):
        raise SystemExit(f"runs have different rounds: {sorted(rounds_a)} vs {sorted(rounds_b)}")
    per_round = []
    for r in sorted(rounds_a):
        wa = from_safetensors(rounds_a[r].read_bytes())
        wb = from_safetensors(rounds_b[r].read_bytes(), list(wa))
        acc_a = metrics_a.get(r, {}).get("global/test_acc")
        acc_b = metrics_b.get(r, {}).get("global/test_acc")
        per_round.append({"round": r, "max_abs_diff": max_abs_diff(wa, wb), "test_acc_a": acc_a, "test_acc_b": acc_b})
    last = per_round[-1]
    worst = max(x["max_abs_diff"] for x in per_round)
    acc_diff_pp = abs(last["test_acc_a"] - last["test_acc_b"]) * 100
    return {"run_a": str(run_a), "run_b": str(run_b), "rounds": len(per_round) - 1,
            "weight_tol": weight_tol, "acc_tol_pp": acc_tol_pp, "max_abs_diff_all_rounds": worst,
            "final_test_acc_a": last["test_acc_a"], "final_test_acc_b": last["test_acc_b"],
            "final_acc_diff_pp": acc_diff_pp, "weights_pass": worst <= weight_tol,
            "metric_pass": acc_diff_pp <= acc_tol_pp,
            "pass": worst <= weight_tol and acc_diff_pp <= acc_tol_pp, "per_round": per_round}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="equivalence_check.py")
    ap.add_argument("run_a")
    ap.add_argument("run_b")
    ap.add_argument("--weight-tol", type=float, default=1e-5)
    ap.add_argument("--acc-tol-pp", type=float, default=0.1)
    ap.add_argument("--json")
    ap.add_argument("--wandb-run", help="ENTITY/PROJECT/RUN_ID whose summary gets the result")
    args = ap.parse_args(argv)
    res = compare(args.run_a, args.run_b, args.weight_tol, args.acc_tol_pp)
    print(f"A: {res['run_a']}\nB: {res['run_b']}")
    print(f"{'round':>5}  {'max_abs_diff':>12}  {'test_acc_a':>10}  {'test_acc_b':>10}")
    for x in res["per_round"]:
        print(f"{x['round']:>5}  {x['max_abs_diff']:>12.3e}  {x['test_acc_a']:>10.4f}  {x['test_acc_b']:>10.4f}")
    print(f"max abs weight diff over all rounds: {res['max_abs_diff_all_rounds']:.3e} (tol {args.weight_tol:g}) "
          f"-> {'PASS' if res['weights_pass'] else 'FAIL'}")
    print(f"final test acc diff: {res['final_acc_diff_pp']:.4f} pp (tol {args.acc_tol_pp:g}) "
          f"-> {'PASS' if res['metric_pass'] else 'FAIL'}")
    print("EQUIVALENCE:", "PASS" if res["pass"] else "FAIL")
    if args.json:
        Path(args.json).write_text(json.dumps(res, indent=1))
    if args.wandb_run:
        import wandb

        run = wandb.Api().run(args.wandb_run)
        run.summary.update({"equivalence/pass": res["pass"],
                            "equivalence/max_abs_weight_diff": res["max_abs_diff_all_rounds"],
                            "equivalence/final_acc_diff_pp": res["final_acc_diff_pp"],
                            "equivalence/reference_run": Path(res["run_b"]).name})
        print(f"wrote equivalence result to W&B run {args.wandb_run}")
    return 0 if res["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())

"""INV-1: a real run equals hub.sim for the same config, seed and participants (stage-2 PLAN.md §2).

compare_runs(real, sim) checks every round's global_ref (sha256 of the global weights); where
the hashes differ it computes the max abs weight difference from the kept checkpoints. Pass:
identical hashes, or a max abs difference <= 1e-5 in every round.
"""

from __future__ import annotations

from flhub_core.weights import max_abs_diff

from .checkpoint import RunDir

TOLERANCE = 1e-5


def round_refs(rd: RunDir) -> dict[int, str]:
    return {r: rd.record(r)["global_ref"] for r in rd.recorded_rounds()}


def compare_runs(real: RunDir, sim: RunDir, tol: float = TOLERANCE) -> dict:
    a, b = round_refs(real), round_refs(sim)
    rounds = sorted(a)
    per_round, worst, missing = [], 0.0, []
    for r in rounds:
        if r not in b:
            missing.append(r)
            continue
        if a[r] == b[r]:
            per_round.append({"round": r, "hash_equal": True, "max_abs_diff": 0.0})
            continue
        if not (real.weights_path(r).exists() and sim.weights_path(r).exists()):
            per_round.append({"round": r, "hash_equal": False, "max_abs_diff": None})
            worst = float("inf")
            continue
        wa, wb = real.load_weights(r), sim.load_weights(r)
        d = max_abs_diff(wa, {k: wb[k] for k in wa})
        worst = max(worst, d)
        per_round.append({"round": r, "hash_equal": False, "max_abs_diff": d})
    hashes_equal = all(x["hash_equal"] for x in per_round) and not missing
    ok = not missing and worst <= tol and len(per_round) == len(rounds)
    return {"method": "full-sim", "real": str(real.path), "sim": str(sim.path), "rounds": len(rounds) - 1,
            "hashes_equal": hashes_equal, "max_abs_diff": worst, "missing_rounds": missing, "tolerance": tol,
            "pass": ok, "per_round": per_round}

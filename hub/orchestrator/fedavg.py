"""Weighted FedAvg (PROJECT_PLAN.md §5)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import numpy as np


def fedavg(updates: Sequence[tuple[str, Mapping[str, np.ndarray], int]],
           names: Sequence[str] | None = None) -> dict[str, np.ndarray]:
    """w_new = sum_k n_k * w_k / sum_k n_k for updates (site_id, weights, n_k).

    Accumulates in float64 in site_id order (so the result does not depend on the order in
    which adapters answered) and returns float32 in `names` order (default: first update's).
    """
    if not updates:
        raise ValueError("FedAvg needs at least one update")
    ordered = sorted(updates, key=lambda u: u[0])
    if len({u[0] for u in ordered}) != len(ordered):
        raise ValueError("duplicate site in FedAvg updates")
    if any(int(n) <= 0 for _, _, n in ordered):
        raise ValueError("num_examples must be positive")
    names = list(names) if names is not None else list(updates[0][1])
    total = np.float64(sum(int(n) for _, _, n in ordered))
    out = {}
    for name in names:
        acc = np.zeros(np.shape(ordered[0][1][name]), dtype=np.float64)
        for _, w, n in ordered:
            acc += np.float64(int(n)) * np.asarray(w[name], dtype=np.float64)
        out[name] = (acc / total).astype(np.float32)
    return out

"""Seeded client sampling (stage-2 PLAN.md §4.3).

The sample of round r is a pure function of (sites, fraction, seed, r): a RandomState (frozen
stream) seeded from sha256(seed, r). There is no sampler state to save, so --resume and
hub.sim reproduce every round's participants exactly; the participants of every round are
also recorded in the run dir, and hub.sim --participants-from replays them.
"""

from __future__ import annotations

import hashlib
import math

import numpy as np


def num_participants(n_sites: int, fraction: float) -> int:
    return max(1, min(n_sites, int(math.floor(fraction * n_sites + 0.5))))


def sample(sites: list[str], fraction: float, seed: int, rnd: int) -> list[str]:
    """The round's participants, in config order."""
    if fraction >= 1.0:
        return list(sites)
    k = num_participants(len(sites), fraction)
    digest = hashlib.sha256(f"flhub/participation:{seed}:{rnd}".encode()).digest()
    rng = np.random.RandomState(int.from_bytes(digest[:4], "little"))
    chosen = set(rng.choice(len(sites), size=k, replace=False).tolist())
    return [s for i, s in enumerate(sites) if i in chosen]

"""Stage-2 site partitions of an official train split (stage-2 EXPERIMENTS.md §Partitions, ADR-009).

make_partition(pid, labels, num_classes, seed) -> {site: sorted indices}; every index is used once.
Deterministic in (pid, labels, seed): only numpy's legacy RandomState is used (its streams are
frozen), seeded from sha256(seed, purpose). The training seeds never change a partition.

iid      stratified, equal sizes
dir<a>   label skew, equal sizes (Hsu et al. 2019): q_k ~ Dirichlet(a * p), p = global class prior;
         site k (in site order) takes round(q_k * n_k) per class without replacement; when a class
         runs out, the shortfall goes to the classes left, in proportion to q_k
qty      stratified (IID labels), sizes proportional to QTY_RATIOS
fwcorr   per class, framework shares ~ Dirichlet(0.3 * 1_3); inside a framework IID halves
mix      the dir0.3 proportions q_k with the qty sizes
iid12    iid over the 12 sites of E5
"""

from __future__ import annotations

import hashlib

import numpy as np

BASE_SITES = ("nvf-h1", "nvf-h2", "v6-h3", "v6-h4", "flw-h5", "flw-h6")
EXTRA_SITES = ("nvf-h7", "nvf-h8", "v6-h9", "v6-h10", "flw-h11", "flw-h12")
FRAMEWORK_SITES = {"nvflare": ("nvf-h1", "nvf-h2"), "vantage6": ("v6-h3", "v6-h4"), "flower": ("flw-h5", "flw-h6")}
QTY_RATIOS = {"nvf-h1": 1, "nvf-h2": 32, "v6-h3": 2, "v6-h4": 16, "flw-h5": 4, "flw-h6": 8}
FWCORR_ALPHA = 0.3
MIX_DIR = "dir0.3"
PIDS = ("iid", "dir1.0", "dir0.3", "dir0.1", "qty", "fwcorr", "mix", "iid12")
DEFINITIONS = {
    "iid": "stratified random split, equal sizes",
    "dir1.0": "label skew, equal sizes: q_k ~ Dirichlet(1.0 p)",
    "dir0.3": "label skew, equal sizes: q_k ~ Dirichlet(0.3 p)",
    "dir0.1": "label skew, equal sizes: q_k ~ Dirichlet(0.1 p)",
    "qty": "IID labels (stratified), sizes 1:32:2:16:4:8 (nvf-h1..flw-h6)",
    "fwcorr": "framework-correlated skew: per class framework shares ~ Dirichlet(0.3 1_3), IID halves inside",
    "mix": "dir0.3 proportions q_k with the qty sizes",
    "iid12": "stratified random split over 12 sites, equal sizes",
}


def sites_of(pid: str) -> list[str]:
    return list(BASE_SITES + EXTRA_SITES) if pid == "iid12" else list(BASE_SITES)


def _rng(seed: int, purpose: str) -> np.random.RandomState:
    digest = hashlib.sha256(f"flhub/partition:{seed}:{purpose}".encode()).digest()
    return np.random.RandomState(int.from_bytes(digest[:4], "little"))


def split_sizes(total: int, weights) -> np.ndarray:
    """Largest-remainder rounding of total * w / sum(w); zero weights get nothing; ties by index."""
    w = np.asarray(weights, dtype=np.float64)
    if total == 0:
        return np.zeros(len(w), dtype=np.int64)
    if not (w >= 0).all() or w.sum() <= 0:
        raise ValueError(f"weights must be >= 0 with a positive sum: {w}")
    exact = total * w / w.sum()
    out = np.floor(exact).astype(np.int64)
    rest = int(total - out.sum())
    order = sorted(range(len(w)), key=lambda i: (-(exact[i] - out[i]), i))
    for i in order[:rest]:
        out[i] += 1
    return out


def _pools(labels: np.ndarray, num_classes: int, rng: np.random.RandomState) -> list[np.ndarray]:
    """Per class, its indices in random order."""
    return [rng.permutation(np.flatnonzero(labels == c)) for c in range(num_classes)]


def _interleave(sizes: np.ndarray) -> np.ndarray:
    """Site index for each of sum(sizes) slots; every site's slots are spread evenly."""
    keys = np.concatenate([(np.arange(n) + 0.5) / n for n in sizes if n > 0])
    owner = np.concatenate([np.full(n, k) for k, n in enumerate(sizes) if n > 0])
    return owner[np.lexsort((owner, keys))]


def _stratified(labels: np.ndarray, num_classes: int, sizes: np.ndarray, rng) -> list[np.ndarray]:
    seq = np.concatenate(_pools(labels, num_classes, rng))  # grouped by class, random inside a class
    owner = _interleave(sizes)
    return [np.sort(seq[owner == k]) for k in range(len(sizes))]


def _dirichlet(rng: np.random.RandomState, alpha: np.ndarray) -> np.ndarray:
    for _ in range(100):  # tiny alphas can underflow every gamma draw to 0
        q = rng.dirichlet(alpha)
        if np.all(np.isfinite(q)) and q.sum() > 0:
            return q
    raise RuntimeError(f"Dirichlet({alpha}) did not give a finite sample")


def dirichlet_proportions(pid: str, class_counts: np.ndarray, num_sites: int, seed: int) -> np.ndarray:
    """q_k ~ Dirichlet(alpha * p) for k = 0..num_sites-1 (one stream per dir pid)."""
    alpha = float(pid[3:])
    prior = class_counts / class_counts.sum()
    rng = _rng(seed, f"q:{pid}")
    return np.stack([_dirichlet(rng, alpha * prior) for _ in range(num_sites)])


def allocate(q: np.ndarray, sizes: np.ndarray, class_counts: np.ndarray) -> np.ndarray:
    """(sites, classes) counts: site k (in order) takes round(q_k * n_k) per class from what is left;
    a class that runs out passes its shortfall to the classes left, in proportion to q_k."""
    avail = class_counts.astype(np.int64).copy()
    counts = np.zeros((len(sizes), len(class_counts)), dtype=np.int64)
    for k, need in enumerate(sizes):
        need = int(need)
        while need > 0:
            weights = np.where(avail > 0, q[k], 0.0)
            if weights.sum() <= 0:  # no proportion left on the open classes: fill by what is left
                weights = avail.astype(np.float64)
            take = np.minimum(split_sizes(need, weights), avail)
            counts[k] += take
            avail -= take
            need -= int(take.sum())
    return counts


def _take(pools: list[np.ndarray], counts: np.ndarray) -> list[np.ndarray]:
    start = np.zeros(len(pools), dtype=np.int64)
    out = []
    for row in counts:
        parts = []
        for c, n in enumerate(row):
            parts.append(pools[c][start[c]:start[c] + n])
            start[c] += n
        out.append(np.sort(np.concatenate(parts)))
    return out


def _fwcorr(labels: np.ndarray, num_classes: int, seed: int) -> dict[str, np.ndarray]:
    rng_q, pools = _rng(seed, "q:fwcorr"), _pools(labels, num_classes, _rng(seed, "pools:fwcorr"))
    parts: dict[str, list[np.ndarray]] = {s: [] for s in BASE_SITES}
    for c in range(num_classes):
        shares = split_sizes(len(pools[c]), _dirichlet(rng_q, np.full(len(FRAMEWORK_SITES), FWCORR_ALPHA)))
        start = 0
        for n, (a, b) in zip(shares, FRAMEWORK_SITES.values()):
            chunk = pools[c][start:start + n]
            start += n
            half = (len(chunk) + (c % 2)) // 2  # odd sizes: the extra sample alternates between a and b
            parts[a].append(chunk[:half])
            parts[b].append(chunk[half:])
    return {s: np.sort(np.concatenate(p)) for s, p in parts.items()}


def make_partition(pid: str, labels: np.ndarray, num_classes: int, seed: int = 2026) -> dict[str, np.ndarray]:
    """{site: sorted indices into the split} for a stage-2 pid."""
    labels = np.asarray(labels).reshape(-1)
    counts = np.bincount(labels, minlength=num_classes)
    sites = sites_of(pid)
    n = len(labels)
    if pid in ("iid", "iid12"):
        parts = _stratified(labels, num_classes, split_sizes(n, np.ones(len(sites))), _rng(seed, f"pools:{pid}"))
    elif pid == "qty":
        sizes = split_sizes(n, [QTY_RATIOS[s] for s in sites])
        parts = _stratified(labels, num_classes, sizes, _rng(seed, "pools:qty"))
    elif pid.startswith("dir") or pid == "mix":
        qpid = MIX_DIR if pid == "mix" else pid
        q = dirichlet_proportions(qpid, counts, len(sites), seed)
        weights = [QTY_RATIOS[s] for s in sites] if pid == "mix" else np.ones(len(sites))
        alloc = allocate(q, split_sizes(n, weights), counts)
        parts = _take(_pools(labels, num_classes, _rng(seed, f"pools:{pid}")), alloc)
    elif pid == "fwcorr":
        return _fwcorr(labels, num_classes, seed)
    else:
        raise KeyError(f"unknown partition {pid!r}; known: {PIDS}")
    return dict(zip(sites, parts))

"""Deterministic local training and evaluation (PROJECT_PLAN.md §5; stage-2 PLAN.md §4.2).

Every client calls train() with the round's global weights and its own data. Same weights,
data, config and torch version => bit-identical result, whichever framework runs it:
one CPU thread, deterministic algorithms, a fresh SGD optimizer per call, and a data order
drawn from a generator seeded with (seed, round, data fingerprint).

Stage 2 adds optional train_config keys (absent = stage-1 behaviour, bit-exact):
    model         cnn-small | cnn-medium | resnet18-gn | payload-<MB>   (flhub_core.model)
    data_variant  <dataset>-<pid>; resolves a lazy SiteSource (flhub_core.data)
    prox_mu       FedProx: minimise f(w) + mu/2 ||w - x||^2 (x = the received global weights)
    weight_decay  SGD weight decay
payload-<MB> models are not trained: w + 1e-3 * g(seed, round, data fingerprint, tensor name).
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Mapping
from dataclasses import asdict, dataclass

import numpy as np
import torch
import torch.nn.functional as F

from .data import DATASET, SiteData, dataset_info, parse_variant, resolve_data
from .model import DEFAULT_MODEL, build_model, check_model, is_payload, make_model, payload_spec, set_weights
from .model import get_weights


@dataclass(frozen=True)
class TrainConfig:
    local_epochs: int = 1
    batch_size: int = 64
    lr: float = 0.05
    momentum: float = 0.0
    seed: int = 2026
    round: int = 0  # hub round number; part of the data-order seed
    model: str = DEFAULT_MODEL
    data_variant: str | None = None
    prox_mu: float = 0.0
    weight_decay: float = 0.0

    @classmethod
    def from_dict(cls, d: Mapping) -> TrainConfig:
        """Build from a dispatch train_config (+ "round"); unknown keys and None/"" values are ignored."""
        types = {"local_epochs": int, "batch_size": int, "lr": float, "momentum": float, "seed": int, "round": int,
                 "model": str, "data_variant": str, "prox_mu": float, "weight_decay": float}
        return cls(**{k: types[k](d[k]) for k in types if k in d and d[k] is not None and d[k] != ""})

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def dataset(self) -> str:
        return parse_variant(self.data_variant)[0] if self.data_variant else DATASET


def configure_determinism() -> None:
    torch.set_num_threads(1)
    torch.use_deterministic_algorithms(True)


def shuffle_seed(seed: int, rnd: int, fingerprint: str) -> int:
    digest = hashlib.sha256(f"flhub/order:{seed}:{rnd}:{fingerprint}".encode()).digest()
    return int.from_bytes(digest[:8], "little") & ((1 << 63) - 1)


def _peak_rss_mb() -> float:
    try:
        import resource

        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0  # Linux: KiB
    except (ImportError, OSError):
        return 0.0


def train(
    weights: Mapping[str, np.ndarray], data, cfg: TrainConfig | Mapping
) -> tuple[dict[str, np.ndarray], int, dict[str, float]]:
    """Local training from `weights` on `data` (SiteData or a load_site() handle).

    Returns (new weights, num_examples, metrics). metrics: train_loss and train_acc over the last
    local epoch, train_time_s (local training only), data_load_s, peak_rss_mb, and data_fp (the
    first 32 bits of the data fingerprint, so the hub can check which data a site trained on).
    """
    cfg = cfg if isinstance(cfg, TrainConfig) else TrainConfig.from_dict(cfg)
    check_model(cfg.model)
    t_load = time.perf_counter()
    site = resolve_data(data, cfg.data_variant)
    n = len(site)
    if n == 0:
        raise ValueError("no training data")
    t0 = time.perf_counter()
    if is_payload(cfg.model):
        new, metrics = _echo(weights, site, cfg), {"train_loss": 0.0, "train_acc": 0.0}
    else:
        new, metrics = _sgd(weights, site, cfg, dataset_info(cfg.dataset).num_classes)
    metrics.update({"train_time_s": time.perf_counter() - t0, "data_load_s": t0 - t_load,
                    "peak_rss_mb": _peak_rss_mb(), "data_fp": int(site.fingerprint[:8], 16)})
    return new, n, metrics


def _sgd(weights: Mapping[str, np.ndarray], data: SiteData, cfg: TrainConfig,
         num_classes: int) -> tuple[dict[str, np.ndarray], dict[str, float]]:
    n = len(data)
    if int(data.y.max()) >= num_classes:
        raise ValueError(f"labels go up to {int(data.y.max())}, but {cfg.dataset} has {num_classes} classes "
                         f"(set train_config data_variant)")
    configure_determinism()
    seed = shuffle_seed(cfg.seed, cfg.round, data.fingerprint)
    torch.manual_seed(seed)
    model = build_model(cfg.model, num_classes)
    set_weights(model, dict(weights))
    model.train()
    params = list(model.parameters())
    opt = torch.optim.SGD(params, lr=cfg.lr, momentum=cfg.momentum, weight_decay=cfg.weight_decay, foreach=False)
    anchor = [p.detach().clone() for p in params] if cfg.prox_mu > 0 else None
    gen = torch.Generator().manual_seed(seed)
    x, y = torch.from_numpy(data.x), torch.from_numpy(data.y)
    loss_sum, correct, seen = 0.0, 0, 0
    for epoch in range(cfg.local_epochs):
        perm = torch.randperm(n, generator=gen)
        last = epoch == cfg.local_epochs - 1
        for start in range(0, n, cfg.batch_size):
            idx = perm[start : start + cfg.batch_size]
            xb, yb = x.index_select(0, idx), y.index_select(0, idx)
            opt.zero_grad(set_to_none=True)
            logits = model(xb)
            loss = F.cross_entropy(logits, yb)
            loss.backward()
            if anchor is not None:  # FedProx: grad of mu/2 ||w - x||^2 is mu (w - x); mu = 0 skips this
                with torch.no_grad():
                    for p, a in zip(params, anchor):
                        p.grad.add_(p - a, alpha=cfg.prox_mu)
            opt.step()
            if last:
                loss_sum += float(loss.detach()) * len(idx)
                correct += int((logits.detach().argmax(dim=1) == yb).sum())
                seen += len(idx)
    return get_weights(model), {"train_loss": loss_sum / seen, "train_acc": correct / seen}


def _echo(weights: Mapping[str, np.ndarray], data: SiteData, cfg: TrainConfig) -> dict[str, np.ndarray]:
    """payload-<MB>: w + 1e-3 * c per tensor, c in [-1, 1) from sha256(seed, round, fingerprint, name)."""
    want = payload_spec(cfg.model)
    got = [(k, tuple(np.shape(v))) for k, v in weights.items()]
    if got != want:
        raise ValueError(f"{cfg.model}: weights do not match its tensor spec ({len(got)} vs {len(want)} tensors)")
    out = {}
    for name, w in weights.items():
        digest = hashlib.sha256(f"flhub/echo:{cfg.seed}:{cfg.round}:{data.fingerprint}:{name}".encode()).digest()
        c = int.from_bytes(digest[:8], "little") / 2.0**63 - 1.0
        out[name] = np.asarray(w, dtype=np.float32) + np.float32(1e-3 * c)
    return out


@torch.no_grad()
def evaluate(weights: Mapping[str, np.ndarray], data: SiteData, batch_size: int = 1024) -> dict[str, float]:
    """Stage-1 evaluation of the binary cnn-small: loss (mean cross-entropy), acc, auc (class 1)."""
    configure_determinism()
    model = make_model()
    set_weights(model, dict(weights))
    model.eval()
    x, y = torch.from_numpy(data.x), torch.from_numpy(data.y)
    n = len(data)
    loss_sum, correct, scores = 0.0, 0, []
    for start in range(0, n, batch_size):
        logits = model(x[start : start + batch_size])
        yb = y[start : start + batch_size]
        loss_sum += float(F.cross_entropy(logits, yb, reduction="sum"))
        correct += int((logits.argmax(dim=1) == yb).sum())
        scores.append(torch.softmax(logits, dim=1)[:, 1].numpy())
    return {"loss": loss_sum / n, "acc": correct / n, "auc": binary_auc(data.y, np.concatenate(scores))}


def binary_auc(labels: np.ndarray, scores: np.ndarray) -> float:
    """ROC AUC via the rank-sum statistic (average ranks for ties)."""
    labels = np.asarray(labels).reshape(-1)
    scores = np.asarray(scores, dtype=np.float64).reshape(-1)
    pos = labels == 1
    n_pos, n_neg = int(pos.sum()), int((~pos).sum())
    if n_pos == 0 or n_neg == 0:
        return float("nan")
    _, inverse, counts = np.unique(scores, return_inverse=True, return_counts=True)
    avg_rank = np.cumsum(counts) - (counts - 1) / 2.0
    ranks = avg_rank[inverse]
    return float((ranks[pos].sum() - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg))

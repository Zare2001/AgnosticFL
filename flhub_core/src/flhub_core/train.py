"""Deterministic local training and evaluation (PROJECT_PLAN.md §5).

Every client calls train() with the round's global weights and its own data. Same weights,
data, config and torch version => bit-identical result, whichever framework runs it:
one CPU thread, deterministic algorithms, a fresh SGD optimizer per call, and a data order
drawn from a generator seeded with (seed, round, data fingerprint).
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import asdict, dataclass

import numpy as np
import torch
import torch.nn.functional as F

from .data import SiteData
from .model import get_weights, make_model, set_weights


@dataclass(frozen=True)
class TrainConfig:
    local_epochs: int = 1
    batch_size: int = 64
    lr: float = 0.05
    momentum: float = 0.0
    seed: int = 2026
    round: int = 0  # hub round number; part of the data-order seed

    @classmethod
    def from_dict(cls, d: Mapping) -> TrainConfig:
        """Build from a dispatch train_config (+ "round"); unknown keys are ignored."""
        types = {"local_epochs": int, "batch_size": int, "lr": float, "momentum": float, "seed": int, "round": int}
        return cls(**{k: types[k](d[k]) for k in types if k in d})

    def to_dict(self) -> dict:
        return asdict(self)


def configure_determinism() -> None:
    torch.set_num_threads(1)
    torch.use_deterministic_algorithms(True)


def shuffle_seed(seed: int, rnd: int, fingerprint: str) -> int:
    digest = hashlib.sha256(f"flhub/order:{seed}:{rnd}:{fingerprint}".encode()).digest()
    return int.from_bytes(digest[:8], "little") & ((1 << 63) - 1)


def train(
    weights: Mapping[str, np.ndarray], data: SiteData, cfg: TrainConfig | Mapping
) -> tuple[dict[str, np.ndarray], int, dict[str, float]]:
    """Local training from `weights`. Returns (new weights, num_examples, metrics).

    metrics: train_loss and train_acc over the last local epoch (computed on the fly).
    """
    cfg = cfg if isinstance(cfg, TrainConfig) else TrainConfig.from_dict(cfg)
    n = len(data)
    if n == 0:
        raise ValueError("no training data")
    configure_determinism()
    seed = shuffle_seed(cfg.seed, cfg.round, data.fingerprint)
    torch.manual_seed(seed)
    model = make_model()
    set_weights(model, dict(weights))
    model.train()
    opt = torch.optim.SGD(model.parameters(), lr=cfg.lr, momentum=cfg.momentum, foreach=False)
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
            opt.step()
            if last:
                loss_sum += float(loss.detach()) * len(idx)
                correct += int((logits.detach().argmax(dim=1) == yb).sum())
                seen += len(idx)
    return get_weights(model), n, {"train_loss": loss_sum / seen, "train_acc": correct / seen}


@torch.no_grad()
def evaluate(weights: Mapping[str, np.ndarray], data: SiteData, batch_size: int = 1024) -> dict[str, float]:
    """loss (mean cross-entropy), acc, auc (class 1 = pneumonia)."""
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

"""Small CNN for 1x28x28 inputs. No BatchNorm, so FedAvg needs no buffer semantics."""

from __future__ import annotations

import numpy as np
import torch
import torch.nn.functional as F
from torch import nn

from .data import NUM_CLASSES


class Net(nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.conv1 = nn.Conv2d(1, 8, kernel_size=3, padding=1)
        self.conv2 = nn.Conv2d(8, 16, kernel_size=3, padding=1)
        self.fc1 = nn.Linear(16 * 7 * 7, 64)
        self.fc2 = nn.Linear(64, NUM_CLASSES)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = F.max_pool2d(F.relu(self.conv1(x)), 2)
        x = F.max_pool2d(F.relu(self.conv2(x)), 2)
        x = F.relu(self.fc1(torch.flatten(x, 1)))
        return self.fc2(x)


def make_model(seed: int | None = None) -> Net:
    """New model; with a seed the initial weights are reproducible (global RNG untouched)."""
    if seed is None:
        return Net()
    with torch.random.fork_rng(devices=[]):
        torch.manual_seed(seed)
        return Net()


def get_weights(model: nn.Module) -> dict[str, np.ndarray]:
    """Canonical weights: state_dict order, float32 numpy copies."""
    return {k: v.detach().cpu().numpy().astype(np.float32, copy=True) for k, v in model.state_dict().items()}


def set_weights(model: nn.Module, weights: dict[str, np.ndarray]) -> None:
    sd = model.state_dict()
    if set(weights) != set(sd):
        raise ValueError(f"weight names differ: missing {sorted(set(sd) - set(weights))}, "
                         f"unexpected {sorted(set(weights) - set(sd))}")
    tensors = {}
    for name, ref in sd.items():
        arr = np.ascontiguousarray(weights[name])
        if tuple(arr.shape) != tuple(ref.shape) or arr.dtype != np.float32:
            raise ValueError(f"{name}: got {arr.dtype}{tuple(arr.shape)}, want float32{tuple(ref.shape)}")
        tensors[name] = torch.from_numpy(arr)
    model.load_state_dict(tensors, strict=True)


def init_weights(seed: int) -> dict[str, np.ndarray]:
    return get_weights(make_model(seed))


def param_names() -> list[str]:
    return list(Net().state_dict())


def num_parameters() -> int:
    return sum(p.numel() for p in Net().parameters())

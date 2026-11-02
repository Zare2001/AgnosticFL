"""Model zoo. No BatchNorm anywhere, so weights are float32 parameters only.

    cnn-small     the stage-1 CNN (same parameter names and init; Net), 1x28x28
    cnn-medium    wider CNN, ~1.7 M parameters, 1x28x28
    resnet18-gn   ResNet-18, CIFAR-style stem (3x3, stride 1, no max-pool), GroupNorm(32), ~11.2 M
    hello-pt      the SimpleNetwork of NVFlare's hello-pt example (CIFAR-10, 3x32x32), ~62 k
    payload-<MB>  synthetic float32 tensors of <MB> MiB in total (4 MiB each); no torch module,
                  "trained" by flhub_core.train's deterministic echo (transport tests)

A model with a fixed input shape declares it as `input_shape`; the run configuration checks it
against the dataset (flhub_core.data.DatasetInfo.image_shape).

The stage-1 API (Net, make_model(seed), init_weights(seed), param_names(), num_parameters())
keeps its meaning: cnn-small with the stage-1 dataset's 2 classes.
"""

from __future__ import annotations

import re

import numpy as np
import torch
import torch.nn.functional as F
from torch import nn

from .data import NUM_CLASSES

DEFAULT_MODEL = "cnn-small"
MODELS = ("cnn-small", "cnn-medium", "resnet18-gn", "hello-pt")
PAYLOAD_TENSOR_FLOATS = 2**20  # 4 MiB per payload tensor
_PAYLOAD = re.compile(r"payload-(\d+(?:\.\d+)?)")


class Net(nn.Module):
    """cnn-small: conv(1->8)-pool-conv(8->16)-pool-fc(784->64)-fc(64->classes)."""

    input_shape = (1, 28, 28)

    def __init__(self, num_classes: int = NUM_CLASSES, in_channels: int = 1) -> None:
        super().__init__()
        self.conv1 = nn.Conv2d(in_channels, 8, kernel_size=3, padding=1)
        self.conv2 = nn.Conv2d(8, 16, kernel_size=3, padding=1)
        self.fc1 = nn.Linear(16 * 7 * 7, 64)
        self.fc2 = nn.Linear(64, num_classes)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = F.max_pool2d(F.relu(self.conv1(x)), 2)
        x = F.max_pool2d(F.relu(self.conv2(x)), 2)
        x = F.relu(self.fc1(torch.flatten(x, 1)))
        return self.fc2(x)


class CNNMedium(nn.Module):
    """cnn-medium: 2x conv(32) - pool - 2x conv(64) - pool - fc(3136->512) - fc(512->classes)."""

    input_shape = (1, 28, 28)

    def __init__(self, num_classes: int = NUM_CLASSES, in_channels: int = 1) -> None:
        super().__init__()
        self.conv1 = nn.Conv2d(in_channels, 32, kernel_size=3, padding=1)
        self.conv2 = nn.Conv2d(32, 32, kernel_size=3, padding=1)
        self.conv3 = nn.Conv2d(32, 64, kernel_size=3, padding=1)
        self.conv4 = nn.Conv2d(64, 64, kernel_size=3, padding=1)
        self.fc1 = nn.Linear(64 * 7 * 7, 512)
        self.fc2 = nn.Linear(512, num_classes)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = F.max_pool2d(F.relu(self.conv2(F.relu(self.conv1(x)))), 2)
        x = F.max_pool2d(F.relu(self.conv4(F.relu(self.conv3(x)))), 2)
        x = F.relu(self.fc1(torch.flatten(x, 1)))
        return self.fc2(x)


def _gn(channels: int) -> nn.GroupNorm:
    return nn.GroupNorm(32, channels)


class BasicBlock(nn.Module):
    def __init__(self, cin: int, cout: int, stride: int) -> None:
        super().__init__()
        self.conv1 = nn.Conv2d(cin, cout, 3, stride=stride, padding=1, bias=False)
        self.gn1 = _gn(cout)
        self.conv2 = nn.Conv2d(cout, cout, 3, stride=1, padding=1, bias=False)
        self.gn2 = _gn(cout)
        self.shortcut = nn.Sequential()
        if stride != 1 or cin != cout:
            self.shortcut = nn.Sequential(nn.Conv2d(cin, cout, 1, stride=stride, bias=False), _gn(cout))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        out = F.relu(self.gn1(self.conv1(x)))
        out = self.gn2(self.conv2(out))
        return F.relu(out + self.shortcut(x))


class ResNet18GN(nn.Module):
    """ResNet-18 with a CIFAR-style stem and GroupNorm(32) instead of BatchNorm."""

    def __init__(self, num_classes: int = NUM_CLASSES, in_channels: int = 1) -> None:
        super().__init__()
        self.conv1 = nn.Conv2d(in_channels, 64, 3, stride=1, padding=1, bias=False)
        self.gn1 = _gn(64)
        layers, cin = [], 64
        for cout, stride in ((64, 1), (128, 2), (256, 2), (512, 2)):
            layers.append(nn.Sequential(BasicBlock(cin, cout, stride), BasicBlock(cout, cout, 1)))
            cin = cout
        self.layer1, self.layer2, self.layer3, self.layer4 = layers
        self.fc = nn.Linear(512, num_classes)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = F.relu(self.gn1(self.conv1(x)))
        x = self.layer4(self.layer3(self.layer2(self.layer1(x))))
        return self.fc(torch.flatten(F.adaptive_avg_pool2d(x, 1), 1))


class HelloPtNet(nn.Module):
    """hello-pt: the SimpleNetwork of NVFlare's examples/hello-world/hello-pt, for 3x32x32 CIFAR-10.

    conv(3->6, 5)-pool-conv(6->16, 5)-pool-fc(400->120)-fc(120->84)-fc(84->classes). Same layers,
    creation order and parameter names (conv1, conv2, fc1, fc2, fc3) as NVFlare's model.py, so the
    initial weights for a seed and every state dict carry over in both directions. hello-pt
    normalizes its inputs in the data loader (Normalize(0.5, 0.5) on [0, 1] pixels); here the same
    two operations run at the start of forward(), because flhub_core feeds pixel / 255.
    """

    input_shape = (3, 32, 32)

    def __init__(self, num_classes: int = 10, in_channels: int = 3) -> None:
        super().__init__()
        self.conv1 = nn.Conv2d(in_channels, 6, 5)
        self.pool = nn.MaxPool2d(2, 2)
        self.conv2 = nn.Conv2d(6, 16, 5)
        self.fc1 = nn.Linear(16 * 5 * 5, 120)
        self.fc2 = nn.Linear(120, 84)
        self.fc3 = nn.Linear(84, num_classes)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = (x - 0.5) / 0.5
        x = self.pool(F.relu(self.conv1(x)))
        x = self.pool(F.relu(self.conv2(x)))
        x = torch.flatten(x, 1)
        x = F.relu(self.fc1(x))
        x = F.relu(self.fc2(x))
        return self.fc3(x)


_ZOO = {"cnn-small": Net, "cnn-medium": CNNMedium, "resnet18-gn": ResNet18GN, "hello-pt": HelloPtNet}


def input_shape(model: str) -> tuple[int, int, int] | None:
    """The (C, H, W) input a zoo model is built for; None if it adapts (resnet18-gn) or has no module."""
    return None if is_payload(model) else getattr(_ZOO[check_model(model)], "input_shape", None)


def is_payload(model: str) -> bool:
    return bool(_PAYLOAD.fullmatch(model or ""))


def payload_mb(model: str) -> float:
    m = _PAYLOAD.fullmatch(model)
    if not m:
        raise ValueError(f"{model!r} is not payload-<MB>")
    return float(m.group(1))


def payload_spec(model: str) -> list[tuple[str, tuple[int]]]:
    """(name, shape) of the payload tensors: 4 MiB each, the last one holds the remainder."""
    total = int(round(payload_mb(model) * 2**20 / 4))
    if total <= 0:
        raise ValueError(f"{model}: payload must be > 0 MiB")
    sizes = [PAYLOAD_TENSOR_FLOATS] * (total // PAYLOAD_TENSOR_FLOATS)
    if total % PAYLOAD_TENSOR_FLOATS:
        sizes.append(total % PAYLOAD_TENSOR_FLOATS)
    return [(f"t{i:04d}", (n,)) for i, n in enumerate(sizes)]


def check_model(model: str) -> str:
    if model not in _ZOO and not is_payload(model):
        hint = f"; plugins not loaded: {_plugins.failed}" if _plugins.failed else ""
        raise KeyError(f"unknown model {model!r}; known: {sorted(_ZOO)} and payload-<MB>{hint}")
    return model


def register_model(name: str, cls: type) -> None:
    """Adds a model class to the zoo under `name` (for plugins, flhub_core.plugins). The class takes
    (num_classes, in_channels) keyword arguments and should declare `input_shape`."""
    if is_payload(name) or not name:
        raise ValueError(f"invalid model name {name!r}")
    if _ZOO.get(name, cls) is not cls:
        raise ValueError(f"model {name!r} is already registered")
    _ZOO[name] = cls


def build_model(model: str = DEFAULT_MODEL, num_classes: int = NUM_CLASSES,
                in_channels: int | None = None) -> nn.Module:
    """A zoo model; in_channels defaults to the model's own (1 for the MedMNIST models, 3 for hello-pt)."""
    if is_payload(model):
        raise ValueError(f"{model} has no torch module (echo model)")
    extra = {} if in_channels is None else {"in_channels": in_channels}
    return _ZOO[check_model(model)](num_classes=num_classes, **extra)


def make_model(seed: int | None = None, model: str = DEFAULT_MODEL, num_classes: int = NUM_CLASSES) -> nn.Module:
    """New model; with a seed the initial weights are reproducible (global RNG untouched)."""
    if seed is None:
        return build_model(model, num_classes)
    with torch.random.fork_rng(devices=[]):
        torch.manual_seed(seed)
        return build_model(model, num_classes)


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


def init_weights(seed: int, model: str = DEFAULT_MODEL, num_classes: int = NUM_CLASSES) -> dict[str, np.ndarray]:
    """Initial global weights. payload-<MB>: standard normal * 0.01 from numpy PCG64(seed) (incompressible)."""
    if is_payload(model):
        rng = np.random.Generator(np.random.PCG64(seed))
        out = {}
        for name, shape in payload_spec(model):
            arr = rng.standard_normal(shape, dtype=np.float32)
            arr *= np.float32(0.01)
            out[name] = arr
        return out
    return get_weights(make_model(seed, model, num_classes))


def param_names(model: str = DEFAULT_MODEL, num_classes: int = NUM_CLASSES) -> list[str]:
    if is_payload(model):
        return [n for n, _ in payload_spec(model)]
    return list(build_model(model, num_classes).state_dict())


def num_parameters(model: str = DEFAULT_MODEL, num_classes: int = NUM_CLASSES) -> int:
    if is_payload(model):
        return sum(s[0] for _, s in payload_spec(model))
    return sum(p.numel() for p in build_model(model, num_classes).parameters())


# Plugins (flhub_core/plugins/*.py) register their models into the zoo above, so they load last.
from . import plugins as _plugins  # noqa: E402

_plugins.load()

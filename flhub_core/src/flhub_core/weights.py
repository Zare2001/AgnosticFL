"""Canonical weights io: ordered {state_dict name: float32 ndarray} <-> safetensors bytes.

A blob is identified by `sha256:<hex>` of its bytes. The tensor order is kept in the
safetensors metadata (key "order"), so from_safetensors() returns the original order.
No torch import here: adapters and the hub can use this module cheaply.
"""

from __future__ import annotations

import base64
import hashlib
from collections.abc import Mapping, Sequence

import numpy as np
from safetensors.numpy import load as _st_load
from safetensors.numpy import save as _st_save

Weights = dict[str, np.ndarray]


def to_safetensors(weights: Mapping[str, np.ndarray]) -> bytes:
    for name, arr in weights.items():
        if not isinstance(arr, np.ndarray) or arr.dtype != np.float32:
            raise TypeError(f"{name}: canonical weights are float32 numpy arrays, got "
                            f"{getattr(arr, 'dtype', type(arr).__name__)}; convert with as_float32()")
    tensors = {k: np.ascontiguousarray(v) for k, v in weights.items()}
    return _st_save(tensors, metadata={"order": ",".join(tensors)})


def from_safetensors(data: bytes, names: Sequence[str] | None = None) -> Weights:
    """Weights in `names` order, else in the stored order."""
    loaded = _st_load(bytes(data))
    if names is None:
        names = _stored_order(data) or list(loaded)
    if set(names) != set(loaded):
        raise ValueError(f"tensor names differ: missing {sorted(set(names) - set(loaded))}, "
                         f"unexpected {sorted(set(loaded) - set(names))}")
    return {k: loaded[k] for k in names}


def _stored_order(data: bytes) -> list[str] | None:
    import json
    import struct

    (n,) = struct.unpack("<Q", bytes(data[:8]))
    header = json.loads(bytes(data[8 : 8 + n]))
    order = (header.get("__metadata__") or {}).get("order")
    return order.split(",") if order else None


def as_float32(weights: Mapping[str, object]) -> Weights:
    """Convert any array-likes (lists, float64, torch tensors via np.asarray) to canonical form."""
    return {k: np.ascontiguousarray(np.asarray(v), dtype=np.float32) for k, v in weights.items()}


def sha256_ref(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def to_b64(weights: Mapping[str, np.ndarray]) -> str:
    """base64(safetensors) for frameworks that move JSON/strings (vantage6)."""
    return base64.b64encode(to_safetensors(weights)).decode("ascii")


def from_b64(text: str, names: Sequence[str] | None = None) -> Weights:
    return from_safetensors(base64.b64decode(text), names)


def spec(weights: Mapping[str, np.ndarray]) -> list[dict]:
    """Model spec as registered at the hub core: ordered names, shapes, dtypes."""
    return [{"name": k, "shape": list(v.shape), "dtype": str(v.dtype)} for k, v in weights.items()]


def spec_problems(weights: Mapping[str, np.ndarray], model_spec: Sequence[Mapping]) -> list[str]:
    """Differences between weights and a spec; empty list = conforms."""
    want = {s["name"]: (tuple(s["shape"]), s["dtype"]) for s in model_spec}
    got = {k: (tuple(v.shape), str(v.dtype)) for k, v in weights.items()}
    problems = [f"missing tensor {k}" for k in want if k not in got]
    problems += [f"unexpected tensor {k}" for k in got if k not in want]
    problems += [f"{k}: got {got[k][1]}{got[k][0]}, want {want[k][1]}{want[k][0]}"
                 for k in want if k in got and got[k] != want[k]]
    return problems


def max_abs_diff(a: Mapping[str, np.ndarray], b: Mapping[str, np.ndarray]) -> float:
    if set(a) != set(b):
        raise ValueError("weights have different tensor names")
    worst = 0.0
    for k in a:
        if a[k].shape != b[k].shape:
            raise ValueError(f"{k}: shapes {a[k].shape} vs {b[k].shape}")
        if a[k].size:
            worst = max(worst, float(np.max(np.abs(a[k].astype(np.float64) - b[k].astype(np.float64)))))
    return worst

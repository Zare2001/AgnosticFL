"""Aggregator interface, pseudo-gradient and the canonical serialization of aggregator state."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence

import numpy as np
from safetensors.numpy import load as _st_load
from safetensors.numpy import save as _st_save

Result = tuple  # (site_id, weights, num_examples, metrics)


class AggregatorError(ValueError):
    pass


class Aggregator:
    """Base class. Subclasses set `name`, `defaults` and implement aggregate()."""

    name = "base"
    defaults: dict = {}
    required: tuple[str, ...] = ()

    def __init__(self, params: Mapping | None = None) -> None:
        params = dict(params or {})
        unknown = sorted(set(params) - set(self.defaults) - set(self.required))
        missing = [k for k in self.required if k not in params]
        if unknown or missing:
            raise AggregatorError(f"{self.name}: unknown params {unknown}, missing params {missing}")
        self.params = {**self.defaults, **{k: float(v) for k, v in params.items()}}

    def client_config(self) -> dict:
        """Extra train_config keys the sites need (FedProx: prox_mu)."""
        return {}

    def init(self, global_weights: Mapping[str, np.ndarray], params: Mapping | None = None) -> dict:
        """Initial state for these global weights (params, if given, replace the constructor's)."""
        if params is not None:
            self.__init__(params)
        return {}

    def aggregate(self, global_weights: Mapping[str, np.ndarray], results: Sequence[Result], state: dict,
                  rnd: int) -> tuple[dict[str, np.ndarray], dict]:
        raise NotImplementedError


def check_results(results: Sequence[Result]) -> list[Result]:
    """Results sorted by site id (so arrival order never matters); refuse empties and bad counts."""
    if not results:
        raise AggregatorError("aggregation needs at least one result")
    ordered = sorted(results, key=lambda r: r[0])
    if len({r[0] for r in ordered}) != len(ordered):
        raise AggregatorError("duplicate site in results")
    if any(int(r[2]) <= 0 for r in ordered):
        raise AggregatorError("num_examples must be positive")
    return ordered


def pseudo_gradient(x: Mapping[str, np.ndarray], results: Sequence[Result]) -> dict[str, np.ndarray]:
    """Delta = sum_k n_k (w_k - x) / sum_k n_k in float64, sites in site-id order."""
    ordered = check_results(results)
    total = np.float64(sum(int(r[2]) for r in ordered))
    delta = {}
    for name, xv in x.items():
        x64 = np.asarray(xv, dtype=np.float64)
        acc = np.zeros(x64.shape, dtype=np.float64)
        for _, w, n, *_ in ordered:
            acc += np.float64(int(n)) * (np.asarray(w[name], dtype=np.float64) - x64)
        delta[name] = acc / total
    return delta


def state_bytes(state: Mapping) -> bytes:
    """Canonical bytes of a state: float64 arrays as safetensors, scalars in the metadata."""
    arrays, scalars = {}, {}
    for k, v in state.items():
        if isinstance(v, np.ndarray):
            if v.dtype != np.float64:
                raise AggregatorError(f"state {k}: arrays must be float64, got {v.dtype}")
            arrays[k] = np.ascontiguousarray(v)
        elif isinstance(v, (int, float)) and not isinstance(v, bool):
            scalars[k] = v
        else:
            raise AggregatorError(f"state {k}: unsupported type {type(v).__name__}")
    # one metadata key only: safetensors keeps metadata in a hash map, so several keys come out in
    # varying order and the bytes (and state_ref) would not be reproducible
    meta = {"flhub_state": json.dumps({"order": list(arrays), "scalars": scalars}, sort_keys=True)}
    return _st_save(arrays, metadata=meta)


def state_from_bytes(data: bytes) -> dict:
    import struct

    (n,) = struct.unpack("<Q", bytes(data[:8]))
    header = json.loads(bytes(data[8:8 + n]))
    meta = json.loads((header.get("__metadata__") or {}).get("flhub_state") or "{}")
    arrays = _st_load(bytes(data))
    state = {k: arrays[k] for k in meta.get("order", [])}
    state.update(meta.get("scalars", {}))
    return state


def state_ref(state: Mapping) -> str:
    return "sha256:" + hashlib.sha256(state_bytes(state)).hexdigest()

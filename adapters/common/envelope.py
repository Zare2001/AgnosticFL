"""Envelope (one per site per round, PROTOCOL.md §3) and its validation."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field

import numpy as np
from flhub_core.weights import spec_problems

from .protocol import PROTOCOL, SUPPORTED_PROTOCOLS, is_ref, utc_now


@dataclass
class Envelope:
    round: int
    site_id: str
    framework: str
    base_model: str
    weights: str
    num_examples: int
    metrics: dict[str, float]
    train_config_hash: str
    client_code_version: str
    created_at: str = field(default_factory=utc_now)
    protocol: str = PROTOCOL

    def to_dict(self) -> dict:
        d = asdict(self)
        order = ["protocol", "round", "site_id", "framework", "base_model", "weights", "num_examples",
                 "metrics", "train_config_hash", "client_code_version", "created_at"]
        return {k: d[k] for k in order}

    @classmethod
    def from_dict(cls, d: Mapping) -> Envelope:
        names = {f for f in cls.__dataclass_fields__}
        missing = [k for k in ("round", "site_id", "framework", "base_model", "weights", "num_examples")
                   if k not in d]
        if missing:
            raise ValueError(f"envelope lacks {missing}")
        return cls(**{k: d[k] for k in d if k in names})


def envelope_problems(
    env: Envelope,
    *,
    round: int,
    base_model: str,
    expected_sites: Iterable[str],
    train_config_hash: str | None = None,
    protocol: str | None = None,
) -> list[str]:
    """Metadata checks the orchestrator applies before FedAvg (weights are checked separately).

    protocol: the version the round was dispatched with (default: any supported version).
    """
    p = []
    if protocol is not None and env.protocol != protocol:
        p.append(f"protocol {env.protocol!r} != {protocol}")
    elif env.protocol not in SUPPORTED_PROTOCOLS:
        p.append(f"protocol {env.protocol!r} not in {SUPPORTED_PROTOCOLS}")
    if env.round != round:
        p.append(f"round {env.round} != {round}")
    if env.base_model != base_model:
        p.append(f"base_model {env.base_model} != round model {base_model}")
    if env.site_id not in set(expected_sites):
        p.append(f"site {env.site_id!r} not expected this round")
    if not is_ref(env.weights):
        p.append(f"weights {env.weights!r} is not a sha256 ref")
    if not isinstance(env.num_examples, int) or isinstance(env.num_examples, bool) or env.num_examples <= 0:
        p.append(f"num_examples {env.num_examples!r} must be a positive int")
    if not isinstance(env.metrics, Mapping) or not all(
        isinstance(v, (int, float)) and not isinstance(v, bool) for v in env.metrics.values()
    ):
        p.append("metrics must map names to numbers")
    if train_config_hash is not None and env.train_config_hash != train_config_hash:
        p.append(f"train_config_hash {env.train_config_hash} != {train_config_hash}")
    return p


def weights_problems(weights: Mapping[str, np.ndarray], model_spec: Sequence[Mapping]) -> list[str]:
    """Names/shapes/dtypes must match the registered spec; values must be finite."""
    p = spec_problems(weights, model_spec)
    if not p:
        p += [f"{k}: non-finite values" for k, v in weights.items() if not np.all(np.isfinite(v))]
    return p


def finite_metrics(metrics: Mapping) -> dict[str, float]:
    """Numeric metrics only, as floats (NaN/inf dropped); used when building envelopes."""
    out = {}
    for k, v in (metrics or {}).items():
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if math.isfinite(f):
            out[str(k)] = f
    return out

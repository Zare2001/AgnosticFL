"""Server aggregators (stage-2 PLAN.md §4.1): pure numpy, framework-free and stateful.

One implementation serves hub.orchestrator and hub.sim, which keeps INV-1 at 0.0.

    agg = make_aggregator("fedadam", {"eta": 0.01})
    state = agg.init(global_weights)                       # {"name": float64 ndarray | int | float}
    new_global, state = agg.aggregate(global_weights, results, state, rnd)
        # results: [(site_id, weights, num_examples, metrics)] of the sites that took part

With p_k = n_k / sum_j n_j over the participating sites and the pseudo-gradient
Delta = sum_k p_k (w_k - x), all arithmetic and state in float64, new global cast to float32 once:

    fedavg      x <- sum_k p_k w_k                (the stage-1 code path, hub.orchestrator.fedavg)
    fedprox     server = fedavg; clients get train_config prox_mu = mu
    fedavgm     m <- beta m + Delta;  x <- x + eta m                         (Hsu et al. 2019)
    fedadam     m <- b1 m + (1-b1) Delta;  v <- b2 v + (1-b2) Delta^2;  x <- x + eta m / (sqrt(v) + tau)
    fedyogi     as fedadam, but v <- v - (1-b2) Delta^2 sign(v - Delta^2)    (Reddi et al. 2021)
    fedadagrad  v <- v + Delta^2;  x <- x + eta Delta / (sqrt(v) + tau)      (no momentum)
"""

from __future__ import annotations

from .base import Aggregator, AggregatorError, pseudo_gradient, state_bytes, state_from_bytes, state_ref
from .strategies import REGISTRY, FedAdagrad, FedAdam, FedAvg, FedAvgM, FedProx, FedYogi


def make_aggregator(name: str, params: dict | None = None) -> Aggregator:
    if name not in REGISTRY:
        raise AggregatorError(f"unknown aggregator {name!r}; known: {sorted(REGISTRY)}")
    return REGISTRY[name](params)


__all__ = ["Aggregator", "AggregatorError", "FedAdagrad", "FedAdam", "FedAvg", "FedAvgM", "FedProx", "FedYogi",
           "REGISTRY", "make_aggregator", "pseudo_gradient", "state_bytes", "state_from_bytes", "state_ref"]

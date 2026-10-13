"""The stage-2 aggregators (formulas in hub/aggregators/__init__.py and PLAN.md stage 2 §4.1)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import numpy as np

from hub.orchestrator.fedavg import fedavg

from .base import Aggregator, Result, check_results, pseudo_gradient


class FedAvg(Aggregator):
    """x <- sum_k p_k w_k, exactly the stage-1 implementation (x + Delta is not bit-equal)."""

    name = "fedavg"

    def aggregate(self, global_weights, results, state, rnd):
        ordered = check_results(results)
        return fedavg([(r[0], r[1], r[2]) for r in ordered], list(global_weights)), dict(state)


class FedProx(FedAvg):
    """Server side = fedavg; the proximal term lives in flhub_core.train (train_config prox_mu)."""

    name = "fedprox"
    required = ("mu",)

    def client_config(self) -> dict:
        return {"prox_mu": float(self.params["mu"])}


def _new_global(x: Mapping[str, np.ndarray], step: Mapping[str, np.ndarray]) -> dict[str, np.ndarray]:
    """x + step in float64, cast to float32 once."""
    return {k: (np.asarray(v, dtype=np.float64) + step[k]).astype(np.float32) for k, v in x.items()}


class FedAvgM(Aggregator):
    """m <- beta m + Delta; x <- x + eta m (server momentum, Hsu et al. 2019)."""

    name = "fedavgm"
    defaults = {"eta": 1.0, "beta": 0.9}

    def init(self, global_weights, params=None):
        super().init(global_weights, params)
        return {f"m/{k}": np.zeros(np.shape(v), dtype=np.float64) for k, v in global_weights.items()}

    def aggregate(self, global_weights, results: Sequence[Result], state, rnd):
        eta, beta = self.params["eta"], self.params["beta"]
        delta = pseudo_gradient(global_weights, results)
        new_state, step = {}, {}
        for k in global_weights:
            m = beta * state[f"m/{k}"] + delta[k]
            new_state[f"m/{k}"] = m
            step[k] = eta * m
        return _new_global(global_weights, step), new_state


class FedAdam(Aggregator):
    """m <- b1 m + (1-b1) Delta; v <- b2 v + (1-b2) Delta^2; x <- x + eta m / (sqrt(v) + tau)."""

    name = "fedadam"
    defaults = {"eta": 0.01, "beta1": 0.9, "beta2": 0.99, "tau": 1e-3}

    def init(self, global_weights, params=None):
        super().init(global_weights, params)
        tau2 = self.params["tau"] ** 2
        state = {}
        for k, v in global_weights.items():
            state[f"m/{k}"] = np.zeros(np.shape(v), dtype=np.float64)
            state[f"v/{k}"] = np.full(np.shape(v), tau2, dtype=np.float64)
        return state

    def second_moment(self, v: np.ndarray, d2: np.ndarray) -> np.ndarray:
        b2 = self.params["beta2"]
        return b2 * v + (1.0 - b2) * d2

    def aggregate(self, global_weights, results: Sequence[Result], state, rnd):
        eta, b1, tau = self.params["eta"], self.params["beta1"], self.params["tau"]
        delta = pseudo_gradient(global_weights, results)
        new_state, step = {}, {}
        for k in global_weights:
            d = delta[k]
            m = b1 * state[f"m/{k}"] + (1.0 - b1) * d
            v = self.second_moment(state[f"v/{k}"], d * d)
            new_state[f"m/{k}"], new_state[f"v/{k}"] = m, v
            step[k] = eta * m / (np.sqrt(v) + tau)
        return _new_global(global_weights, step), new_state


class FedYogi(FedAdam):
    """As fedadam, but v <- v - (1-b2) Delta^2 sign(v - Delta^2)."""

    name = "fedyogi"

    def second_moment(self, v: np.ndarray, d2: np.ndarray) -> np.ndarray:
        return v - (1.0 - self.params["beta2"]) * d2 * np.sign(v - d2)


class FedAdagrad(Aggregator):
    """v <- v + Delta^2; x <- x + eta Delta / (sqrt(v) + tau) (no momentum)."""

    name = "fedadagrad"
    defaults = {"eta": 0.1, "tau": 1e-3}

    def init(self, global_weights, params=None):
        super().init(global_weights, params)
        tau2 = self.params["tau"] ** 2
        return {f"v/{k}": np.full(np.shape(v), tau2, dtype=np.float64) for k, v in global_weights.items()}

    def aggregate(self, global_weights, results: Sequence[Result], state, rnd):
        eta, tau = self.params["eta"], self.params["tau"]
        delta = pseudo_gradient(global_weights, results)
        new_state, step = {}, {}
        for k in global_weights:
            d = delta[k]
            v = state[f"v/{k}"] + d * d
            new_state[f"v/{k}"] = v
            step[k] = eta * d / (np.sqrt(v) + tau)
        return _new_global(global_weights, step), new_state


REGISTRY = {c.name: c for c in (FedAvg, FedProx, FedAvgM, FedAdam, FedYogi, FedAdagrad)}

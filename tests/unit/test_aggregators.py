"""hub/aggregators against hand-written formulas (PLAN.md stage 2 §4.1)."""

import numpy as np
import pytest

from hub.aggregators import REGISTRY, AggregatorError, make_aggregator, state_bytes, state_from_bytes, state_ref
from hub.orchestrator.fedavg import fedavg

SHAPES = {"a": (3, 4), "b": (5,)}


def weights(rng, scale=1.0):
    return {k: (rng.standard_normal(s) * scale).astype(np.float32) for k, s in SHAPES.items()}


def make_results(rng, x, n=3):
    sites = ["s3", "s1", "s2"][:n]  # unsorted on purpose
    return [(s, {k: (x[k] + rng.standard_normal(x[k].shape).astype(np.float32) * 0.1) for k in x},
             int(rng.randint(5, 50)), {}) for s in sites]


def delta(x, results):
    total = sum(r[2] for r in results)
    out = {}
    for k in x:
        acc = np.zeros(x[k].shape)
        for _, w, n, _ in sorted(results, key=lambda r: r[0]):
            acc += n * (w[k].astype(np.float64) - x[k].astype(np.float64))
        out[k] = acc / total
    return out


def test_fedavg_is_the_stage1_code_path():
    rng = np.random.RandomState(0)
    x = weights(rng)
    res = make_results(rng, x)
    got, state = make_aggregator("fedavg").aggregate(x, res, {}, 1)
    want = fedavg([(s, w, n) for s, w, n, _ in res], list(x))
    assert state == {} and all(np.array_equal(got[k], want[k]) for k in x)
    assert all(got[k].dtype == np.float32 for k in x)


def test_fedprox_server_is_fedavg_and_sends_mu():
    rng = np.random.RandomState(1)
    x = weights(rng)
    res = make_results(rng, x)
    agg = make_aggregator("fedprox", {"mu": 0.01})
    assert agg.client_config() == {"prox_mu": 0.01}
    got, _ = agg.aggregate(x, res, agg.init(x), 1)
    want, _ = make_aggregator("fedavg").aggregate(x, res, {}, 1)
    assert all(np.array_equal(got[k], want[k]) for k in x)
    with pytest.raises(AggregatorError):
        make_aggregator("fedprox")  # mu is required


def run_two_rounds(name, params, formula):
    rng = np.random.RandomState(2)
    agg = make_aggregator(name, params)
    x = weights(rng)
    state = agg.init(x)
    ref_state = formula("init", x, None, None)
    for rnd in (1, 2):
        res = make_results(rng, x)
        x_new, state = agg.aggregate(x, res, state, rnd)
        want_x, ref_state = formula("step", x, delta(x, res), ref_state)
        for k in x:
            assert x_new[k].dtype == np.float32
            np.testing.assert_array_equal(x_new[k], want_x[k].astype(np.float32))
        x = x_new
    return state


def test_fedavgm_formula():
    eta, beta = 0.3, 0.9

    def formula(stage, x, d, st):
        if stage == "init":
            return {k: np.zeros(v.shape) for k, v in x.items()}
        m = {k: beta * st[k] + d[k] for k in x}
        return {k: x[k].astype(np.float64) + eta * m[k] for k in x}, m

    run_two_rounds("fedavgm", {"eta": eta, "beta": beta}, formula)


@pytest.mark.parametrize("name", ["fedadam", "fedyogi"])
def test_fedadam_and_fedyogi_formula(name):
    eta, b1, b2, tau = 0.01, 0.9, 0.99, 1e-3

    def formula(stage, x, d, st):
        if stage == "init":
            return {k: (np.zeros(v.shape), np.full(v.shape, tau**2)) for k, v in x.items()}
        out_x, new = {}, {}
        for k in x:
            m, v = st[k]
            m = b1 * m + (1 - b1) * d[k]
            if name == "fedadam":
                v = b2 * v + (1 - b2) * d[k] ** 2
            else:
                v = v - (1 - b2) * d[k] ** 2 * np.sign(v - d[k] ** 2)
            new[k] = (m, v)
            out_x[k] = x[k].astype(np.float64) + eta * m / (np.sqrt(v) + tau)
        return out_x, new

    state = run_two_rounds(name, {"eta": eta}, formula)
    assert set(state) == {f"{p}/{k}" for p in ("m", "v") for k in SHAPES}


def test_fedadagrad_formula():
    eta, tau = 0.1, 1e-3

    def formula(stage, x, d, st):
        if stage == "init":
            return {k: np.full(v.shape, tau**2) for k, v in x.items()}
        v = {k: st[k] + d[k] ** 2 for k in x}
        return {k: x[k].astype(np.float64) + eta * d[k] / (np.sqrt(v[k]) + tau) for k in x}, v

    run_two_rounds("fedadagrad", {"eta": eta}, formula)


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_one_site_without_change_keeps_the_model(name):
    rng = np.random.RandomState(3)
    x = weights(rng)
    agg = make_aggregator(name, {"mu": 0.1} if name == "fedprox" else None)
    state = agg.init(x)
    for rnd in (1, 2, 3):
        x_new, state = agg.aggregate(x, [("only", {k: v.copy() for k, v in x.items()}, 7, {})], state, rnd)
        assert all(np.array_equal(x_new[k], x[k]) for k in x), name


@pytest.mark.parametrize("name", ["fedavgm", "fedadam", "fedyogi", "fedadagrad"])
def test_state_roundtrip_resumes_bit_exactly(name):
    rng = np.random.RandomState(4)
    x0 = weights(rng)
    rounds = [make_results(np.random.RandomState(10 + r), x0) for r in range(4)]

    def run(stop_after=None):
        agg = make_aggregator(name)
        x, st = x0, agg.init(x0)
        for r, res in enumerate(rounds, 1):
            x, st = agg.aggregate(x, res, st, r)
            if r == stop_after:  # "crash": continue from serialized state with a fresh aggregator
                st = state_from_bytes(state_bytes(st))
                agg = make_aggregator(name)
        return x, st

    (xa, sa), (xb, sb) = run(), run(stop_after=2)
    assert all(np.array_equal(xa[k], xb[k]) for k in xa)
    assert state_ref(sa) == state_ref(sb) and state_bytes(sa) == state_bytes(sb)


def test_state_serialization():
    st = {"m/a": np.arange(3, dtype=np.float64), "t": 3, "lr": 0.5}
    back = state_from_bytes(state_bytes(st))
    assert back["t"] == 3 and back["lr"] == 0.5 and np.array_equal(back["m/a"], st["m/a"])
    assert state_ref({}) == state_ref({}) and state_ref(st) != state_ref({})
    with pytest.raises(AggregatorError):
        state_bytes({"x": np.zeros(2, dtype=np.float32)})


def test_unknown_names_and_params():
    with pytest.raises(AggregatorError):
        make_aggregator("krum")
    with pytest.raises(AggregatorError):
        make_aggregator("fedadam", {"lr": 0.1})

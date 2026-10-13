"""Unit tests for flhub_core (offline: synthetic data, no download)."""

import re

import numpy as np
import pytest

import flhub_core
from flhub_core import data as D
from flhub_core.model import init_weights, num_parameters, param_names
from flhub_core.train import TrainConfig, binary_auc, evaluate, shuffle_seed, train
from flhub_core.weights import (
    as_float32,
    from_b64,
    from_safetensors,
    max_abs_diff,
    sha256_ref,
    spec,
    spec_problems,
    to_b64,
    to_safetensors,
)


def synthetic(n=150, seed=0):
    rng = np.random.RandomState(seed)
    labels = rng.randint(0, 2, size=n)
    pixels = rng.randint(0, 256, size=(n, D.NUM_FEATURES))
    pixels[labels == 1, :100] = 255  # learnable signal
    return D.from_arrays(pixels, labels)


def test_code_version_format():
    assert re.fullmatch(r"\d+\.\d+\.\d+\+[0-9a-f]{12}", flhub_core.code_version())


def test_partition_indices_disjoint_and_complete():
    parts = D.partition_indices(4708)
    assert [len(p) for p in parts] == [785, 785, 785, 785, 784, 784]
    allidx = np.concatenate(parts)
    assert np.array_equal(np.sort(allidx), np.arange(4708))
    assert parts[0][:5].tolist() == D.partition_indices(4708)[0][:5].tolist()


def test_sites_map_to_partitions():
    assert sorted(D.SITES.values()) == list(range(D.NUM_PARTITIONS))
    assert set(D.PARTITION_SHA256) == set(D.SITES)


def test_from_arrays_validates_and_fingerprints():
    d = synthetic()
    assert d.x.shape == (150, 1, 28, 28) and d.x.dtype == np.float32
    assert d.y.dtype == np.int64 and 0.0 <= d.x.min() and d.x.max() <= 1.0
    assert d.fingerprint == synthetic().fingerprint != synthetic(seed=1).fingerprint
    with pytest.raises(ValueError):
        D.from_arrays(np.full((2, D.NUM_FEATURES), 256), np.zeros(2))
    with pytest.raises(ValueError):
        D.from_arrays(np.zeros((2, D.NUM_FEATURES)), np.array([0, 2]), num_classes=2)
    assert len(D.from_arrays(np.zeros((2, D.NUM_FEATURES)), np.array([0, 10]))) == 2  # 11-class data


def test_csv_roundtrip_and_dataframe(tmp_path):
    rng = np.random.RandomState(3)
    pixels, labels = rng.randint(0, 256, size=(7, D.NUM_FEATURES)), rng.randint(0, 2, size=7)
    path = tmp_path / "site.csv"
    path.write_bytes(D.to_csv_bytes(pixels, labels))
    a, b = D.load_site(path), D.from_arrays(pixels, labels)
    assert a.fingerprint == b.fingerprint and np.array_equal(a.x, b.x)
    pd = pytest.importorskip("pandas")
    c = D.load_site(pd.read_csv(path))
    assert c.fingerprint == a.fingerprint and np.array_equal(c.x, a.x)


def test_model_size_and_names():
    assert num_parameters() <= 2_000_000
    w = init_weights(2026)
    assert list(w) == param_names()
    assert all(v.dtype == np.float32 for v in w.values())
    assert sum(v.nbytes for v in w.values()) <= 8 * 2**20
    assert max_abs_diff(w, init_weights(2026)) == 0.0
    assert max_abs_diff(w, init_weights(2027)) > 0.0


def test_safetensors_roundtrip_keeps_order_and_hash():
    w = init_weights(1)
    blob = to_safetensors(w)
    back = from_safetensors(blob)
    assert list(back) == list(w) and max_abs_diff(w, back) == 0.0
    assert sha256_ref(blob) == sha256_ref(to_safetensors(back))
    assert sha256_ref(blob).startswith("sha256:") and len(sha256_ref(blob)) == 71
    assert list(from_b64(to_b64(w))) == list(w)
    with pytest.raises(TypeError):
        to_safetensors({"a": np.zeros(2, dtype=np.float64)})
    assert as_float32({"a": [1, 2]})["a"].dtype == np.float32


def test_spec_problems():
    w = init_weights(1)
    s = spec(w)
    assert spec_problems(w, s) == []
    bad = dict(w)
    bad["fc2.bias"] = np.zeros(3, dtype=np.float32)
    del bad["fc1.bias"]
    probs = spec_problems(bad, s)
    assert any("missing tensor fc1.bias" in p for p in probs) and any(p.startswith("fc2.bias") for p in probs)


def test_train_is_deterministic_and_learns():
    d = synthetic()
    w0 = init_weights(2026)
    cfg = {"local_epochs": 2, "batch_size": 32, "lr": 0.05, "momentum": 0.0, "seed": 2026, "round": 1}
    w1, n, m = train(w0, d, cfg)
    w2, _, _ = train(w0, d, TrainConfig.from_dict(cfg))
    assert n == 150 and max_abs_diff(w1, w2) == 0.0
    assert max_abs_diff(w0, w1) > 0.0
    assert 0.0 <= m["train_acc"] <= 1.0 and np.isfinite(m["train_loss"])
    w3, _, _ = train(w0, d, dict(cfg, round=2))
    assert max_abs_diff(w1, w3) > 0.0  # data order depends on the round


def test_train_config_from_dict_coerces_and_ignores_unknown():
    cfg = TrainConfig.from_dict({"lr": 1, "batch_size": 64.0, "round": "3", "extra": "x"})
    assert cfg.lr == 1.0 and isinstance(cfg.lr, float) and cfg.batch_size == 64 and cfg.round == 3


def test_shuffle_seed_depends_on_inputs():
    a = shuffle_seed(2026, 1, "f")
    assert a == shuffle_seed(2026, 1, "f") and a != shuffle_seed(2026, 2, "f") and a != shuffle_seed(2026, 1, "g")
    assert 0 <= a < 2**63


def test_evaluate_and_auc():
    d = synthetic()
    m = evaluate(init_weights(2026), d)
    assert set(m) == {"loss", "acc", "auc"} and 0.0 <= m["acc"] <= 1.0
    assert binary_auc(np.array([0, 0, 1, 1]), np.array([0.1, 0.4, 0.35, 0.8])) == pytest.approx(0.75)
    assert binary_auc(np.array([0, 1]), np.array([0.5, 0.5])) == pytest.approx(0.5)
    assert np.isnan(binary_auc(np.array([1, 1]), np.array([0.2, 0.3])))

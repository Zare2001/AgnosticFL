"""flhub_core 0.2: lazy site data and variants, partitions, model zoo, training options, echo."""

import numpy as np
import pytest

from flhub_core import data as D
from flhub_core import partition as P
from flhub_core.model import init_weights, num_parameters, param_names, payload_spec
from flhub_core.selfcheck import synthetic_data
from flhub_core.train import TrainConfig, train
from flhub_core.weights import max_abs_diff

CFG = {"local_epochs": 1, "batch_size": 64, "lr": 0.05, "momentum": 0.0, "seed": 2026, "round": 3}
V11 = "organamnist-dir0.3"


def write_csv(path, n, seed, classes=2):
    rng = np.random.RandomState(seed)
    path.write_bytes(D.to_csv_bytes(rng.randint(0, 256, size=(n, D.NUM_FEATURES)), rng.randint(0, classes, n)))
    return path


def test_site_source_file_variants_and_len(tmp_path):
    base = write_csv(tmp_path / "nvf-h1.csv", 9, 0)
    var = write_csv(tmp_path / f"{V11}-nvf-h1.csv", 13, 1, classes=11)
    write_csv(tmp_path / f"{V11}-nvf-h2.csv", 5, 2, classes=11)  # another site in the same directory
    src = D.load_site(base)
    assert isinstance(src, D.SiteSource) and len(src) == 9
    assert src.resolve(None).fingerprint == D.read_csv(base).fingerprint == src.fingerprint
    assert src.resolve(V11).fingerprint == D.read_csv(var).fingerprint and len(src.resolve(V11)) == 13
    with pytest.raises(FileNotFoundError):
        src.resolve("organamnist-iid")
    assert len(D.load_site(tmp_path / "nvf-h7.csv")) == 0  # missing default file: lazy, no error at load


def test_site_source_directory(tmp_path):
    d = tmp_path / "flw-h5"
    d.mkdir()
    write_csv(d / "pneumoniamnist-iid-flw-h5.csv", 7, 3)
    write_csv(d / f"{V11}-flw-h5.csv", 4, 4, classes=11)
    write_csv(d / "organamnist-iid12-flw-h5.csv", 6, 5, classes=11)
    src = D.load_site(d)
    assert len(src) == 7 and len(src.resolve(V11)) == 4 and len(src.resolve("organamnist-iid12")) == 6
    with pytest.raises(FileNotFoundError):
        src.resolve("organamnist-iid")  # 'iid-*' must not match 'iid12-*'


def test_concrete_data_is_used_as_given():
    d = synthetic_data(64)
    assert D.resolve_data(d, V11) is d


def test_parse_variant():
    assert D.parse_variant("organamnist-dir0.1") == ("organamnist", "dir0.1")
    assert D.parse_variant("pneumoniamnist-iid") == ("pneumoniamnist", "iid")
    for bad in ("organamnist-dir0.5", "nosuch-iid", "organamnist"):
        with pytest.raises(KeyError):
            D.parse_variant(bad)


def test_train_config_keeps_stage2_keys():
    cfg = {**CFG, "model": "cnn-medium", "data_variant": V11, "prox_mu": 0.01, "weight_decay": 1e-4, "x": 1}
    tc = TrainConfig.from_dict(cfg)
    assert (tc.model, tc.data_variant, tc.prox_mu, tc.weight_decay, tc.dataset) == (
        "cnn-medium", V11, 0.01, 1e-4, "organamnist")
    assert TrainConfig.from_dict(CFG).model == "cnn-small" and TrainConfig.from_dict(CFG).dataset == "pneumoniamnist"


def test_stage2_train_dict_equals_from_dict_and_mu_zero_is_plain_sgd():
    d = synthetic_data(150)
    w0 = init_weights(2026, "cnn-small", 11)
    cfg = {**CFG, "data_variant": V11, "momentum": 0.9, "weight_decay": 1e-4}
    w1, n, m = train(w0, d, cfg)
    w2, _, _ = train(w0, d, TrainConfig.from_dict(cfg))
    assert n == 150 and max_abs_diff(w1, w2) == 0.0
    w3, _, _ = train(w0, d, {**cfg, "prox_mu": 0.0})
    assert max_abs_diff(w1, w3) == 0.0
    w4, _, _ = train(w0, d, {**cfg, "prox_mu": 0.1})
    assert max_abs_diff(w1, w4) > 0.0
    assert {"train_loss", "train_acc", "train_time_s", "data_load_s", "peak_rss_mb", "data_fp"} <= set(m)
    assert m["data_fp"] == int(d.fingerprint[:8], 16)


def test_wrong_class_count_is_refused():
    d = synthetic_data(64)  # labels 0..10
    with pytest.raises(ValueError, match="classes"):
        train(init_weights(2026), d, CFG)  # no data_variant: the 2-class stage-1 dataset


def test_model_zoo():
    assert num_parameters("cnn-small") == num_parameters()  # stage-1 model by default
    assert 1_000_000 <= num_parameters("cnn-medium", 11) <= 3_000_000
    assert 10_500_000 <= num_parameters("resnet18-gn", 11) <= 11_500_000
    for m in ("cnn-small", "cnn-medium", "resnet18-gn"):
        w = init_weights(1, m, 11)
        assert list(w) == param_names(m, 11) and all(v.dtype == np.float32 for v in w.values())
    assert sum(v.nbytes for v in init_weights(1, "resnet18-gn", 11).values()) / 2**20 == pytest.approx(42.6, abs=1)


def test_payload_spec_init_and_echo():
    assert payload_spec("payload-1") == [("t0000", (262144,))]
    spec9 = payload_spec("payload-9")
    assert [s for _, s in spec9] == [(2**20,), (2**20,), (262144,)]
    w0 = init_weights(5, "payload-9")
    assert sum(v.nbytes for v in w0.values()) == 9 * 2**20 and max_abs_diff(w0, init_weights(5, "payload-9")) == 0
    d = synthetic_data(32)
    cfg = {**CFG, "model": "payload-9"}
    w1, n, m = train(w0, d, cfg)
    w2, _, _ = train(w0, d, cfg)
    assert n == 32 and max_abs_diff(w1, w2) == 0.0 and 0 < max_abs_diff(w0, w1) <= 1e-3 + 1e-6
    w3, _, _ = train(w0, d, {**cfg, "round": 4})
    assert max_abs_diff(w1, w3) > 0
    with pytest.raises(ValueError):
        train(init_weights(5, "payload-1"), d, cfg)


def test_partitions_complete_deterministic_and_sized():
    rng = np.random.RandomState(0)
    labels = rng.choice(11, size=3000, p=np.arange(1, 12) / 66)
    for pid in P.PIDS:
        parts = P.make_partition(pid, labels, 11, 2026)
        assert list(parts) == P.sites_of(pid)
        allidx = np.concatenate(list(parts.values()))
        assert np.array_equal(np.sort(allidx), np.arange(len(labels))), pid
        again = P.make_partition(pid, labels, 11, 2026)
        assert all(np.array_equal(parts[s], again[s]) for s in parts)
    sizes = {s: len(i) for s, i in P.make_partition("qty", labels, 11).items()}
    assert sizes == dict(zip(P.BASE_SITES, P.split_sizes(3000, [1, 32, 2, 16, 4, 8]).tolist()))
    assert len({len(i) for i in P.make_partition("dir0.3", labels, 11).values()}) <= 2  # equal sizes (+-1)
    assert {len(i) for i in P.make_partition("iid12", labels, 11).values()} == {250}
    assert any(not np.array_equal(P.make_partition("dir0.3", labels, 11, 2026)[s],
                                  P.make_partition("dir0.3", labels, 11, 2027)[s]) for s in P.BASE_SITES)


def test_mix_uses_the_dir03_proportions():
    counts = np.array([50, 80, 120, 300], dtype=np.int64)
    q = P.dirichlet_proportions("dir0.3", counts, 6, 2026)
    assert q.shape == (6, 4) and np.allclose(q.sum(axis=1), 1)
    alloc = P.allocate(q, P.split_sizes(int(counts.sum()), [1, 32, 2, 16, 4, 8]), counts)
    assert (alloc.sum(axis=0) == counts).all() and (alloc >= 0).all()


def test_split_sizes():
    assert P.split_sizes(10, [1, 1, 1]).tolist() == [4, 3, 3]
    assert P.split_sizes(5, [0, 1, 1]).tolist() == [0, 3, 2]

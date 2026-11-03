"""hello-pt on the hub: CIFAR-10-shaped data (3x32x32), the hello-pt model, configs and kits' data tool."""

import io
import pickle
import tarfile
from pathlib import Path

import numpy as np
import pytest
import yaml

from flhub_core import data as D
from flhub_core.model import build_model, init_weights, input_shape, num_parameters, param_names
from flhub_core.train import TrainConfig, train

CIFAR = 3 * 32 * 32
CONFIG = Path(__file__).resolve().parents[2] / "configs" / "hello-pt.yaml"
CFG = {"local_epochs": 1, "batch_size": 32, "lr": 0.01, "momentum": 0.9, "seed": 202610, "round": 1,
       "model": "hello-pt", "data_variant": "cifar10-iid"}


def rgb(n, seed, classes=10):
    rng = np.random.RandomState(seed)
    return rng.randint(0, 256, size=(n, CIFAR)), rng.randint(0, classes, n)


def test_cifar_rows_are_3x32x32_channel_first():
    px, lab = rgb(5, 0)
    d = D.from_arrays(px, lab, num_classes=10)
    assert d.x.shape == (5, 3, 32, 32) and d.x.dtype == np.float32
    assert np.array_equal(d.x[2, 1].ravel(), px[2, 1024:2048].astype(np.float32) / np.float32(255.0))  # G plane
    with pytest.raises(ValueError):
        D.from_arrays(np.zeros((2, 1000)), np.zeros(2))  # a width of no dataset
    assert D.image_shape(784) == (1, 28, 28) and D.image_shape(CIFAR) == (3, 32, 32)


def test_cifar_csv_and_dataframe_round_trip(tmp_path):
    px, lab = rgb(6, 1)
    path = tmp_path / "cifar10-iid-flw-h5.csv"
    path.write_bytes(D.to_csv_bytes(px, lab))
    assert path.read_text().splitlines()[0] == ",".join(D.columns(CIFAR))
    a = D.read_csv(path)
    assert a.fingerprint == D.from_arrays(px, lab).fingerprint and a.x.shape == (6, 3, 32, 32)
    src = D.load_site(tmp_path / "flw-h5.csv")  # default file missing: the variant file next to it is used
    assert src.resolve("cifar10-iid").fingerprint == a.fingerprint
    pd = pytest.importorskip("pandas")
    df = pd.read_csv(path)
    assert D.load_site(df).fingerprint == a.fingerprint


def test_medmnist_csv_unchanged():
    rng = np.random.RandomState(3)
    px, lab = rng.randint(0, 256, size=(4, D.NUM_FEATURES)), rng.randint(0, 2, 4)
    assert D.to_csv_bytes(px, lab).splitlines()[0].decode() == ",".join(D.COLUMNS)
    assert D.from_arrays(px, lab).x.shape == (4, 1, 28, 28)


def test_cifar10_variants_registered_but_not_in_all():
    assert D.parse_variant("cifar10-iid") == ("cifar10", "iid")
    assert D.parse_variant("cifar10-dir0.3") == ("cifar10", "dir0.3")
    assert not any(v.startswith("cifar10") for v in D.all_variants())
    assert D.dataset_info("cifar10").num_classes == 10
    with pytest.raises(KeyError):
        D.load_split("val", dataset="cifar10")  # CIFAR-10 has no val split (checked before any download)


def fake_cifar_tarball(path, n_per_batch=4):
    """Same layout as cifar-10-python.tar.gz: pickled dicts with b'data' (n, 3072) uint8 and b'labels'."""
    rng = np.random.RandomState(7)
    with tarfile.open(path, "w:gz") as tar:
        for name in [f"data_batch_{i}" for i in range(1, 6)] + ["test_batch"]:
            blob = pickle.dumps({b"batch_label": name.encode(), b"labels": rng.randint(0, 10, n_per_batch).tolist(),
                                 b"data": rng.randint(0, 256, (n_per_batch, CIFAR)).astype(np.uint8)}, protocol=2)
            info = tarfile.TarInfo(f"cifar-10-batches-py/{name}")
            info.size = len(blob)
            tar.addfile(info, io.BytesIO(blob))


def test_cifar_tarball_reader(tmp_path):
    tgz = tmp_path / "cifar-10-python.tar.gz"
    fake_cifar_tarball(tgz)
    x, y = D._cifar_split(tgz, "train")
    assert x.shape == (20, CIFAR) and x.dtype == np.uint8 and y.shape == (20,)
    assert D._cifar_split(tgz, "test")[0].shape == (4, CIFAR)


def test_cifar_reader_rejects_other_pickled_objects(tmp_path):
    tgz = tmp_path / "evil.tar.gz"
    blob = pickle.dumps({b"data": np.zeros((1, CIFAR), np.uint8), b"labels": [0], b"x": io.BytesIO(b"")})
    with tarfile.open(tgz, "w:gz") as tar:
        info = tarfile.TarInfo("cifar-10-batches-py/test_batch")
        info.size = len(blob)
        tar.addfile(info, io.BytesIO(blob))
    with pytest.raises(pickle.UnpicklingError):
        D._cifar_split(tgz, "test")


def test_hello_pt_model():
    assert num_parameters("hello-pt", 10) == 62006
    assert input_shape("hello-pt") == (3, 32, 32) and input_shape("cnn-small") == (1, 28, 28)
    assert param_names("hello-pt", 10) == ["conv1.weight", "conv1.bias", "conv2.weight", "conv2.bias",
                                           "fc1.weight", "fc1.bias", "fc2.weight", "fc2.bias", "fc3.weight", "fc3.bias"]
    import torch

    out = build_model("hello-pt", 10)(torch.zeros(2, 3, 32, 32))
    assert out.shape == (2, 10)
    assert build_model("cnn-small", 2).conv1.in_channels == 1  # the MedMNIST models keep 1 channel


def test_hello_pt_training_is_deterministic():
    px, lab = rgb(70, 2)
    data = D.from_arrays(px, lab)
    w0 = init_weights(202610, "hello-pt", 10)
    a, n, m = train(w0, data, TrainConfig.from_dict(CFG))
    b, _, _ = train(w0, data, TrainConfig.from_dict(CFG))
    assert n == 70 and set(a) == set(w0) and m["train_loss"] > 0
    assert all(np.array_equal(a[k], b[k]) for k in a)
    assert any(not np.array_equal(a[k], w0[k]) for k in a)


def test_run_config_checks_model_and_data(tmp_path):
    from hub.orchestrator.config import load_config

    good = load_config(CONFIG)
    assert good.data_variant == "cifar10-iid" and good.num_classes == 10
    assert good.train_config["model"] == "hello-pt" and good.train_config["momentum"] == 0.9
    raw = yaml.safe_load(open(CONFIG))
    for bad, msg in (({"model": "cnn-small"}, "takes 1x28x28"), ({"partition": None}, "needs a partition")):
        p = tmp_path / "bad.yaml"
        p.write_text(yaml.safe_dump({**raw, **bad}))
        with pytest.raises(ValueError, match=msg):
            load_config(p)


def test_hospital_owned_dataset_needs_no_manifest_and_no_hub_eval(tmp_path, monkeypatch):
    """A dataset without a public copy (url "") runs without a partition manifest, with eval off."""
    from hub.orchestrator.config import load_config
    from hub.orchestrator.run import manifest

    monkeypatch.setitem(D.DATASETS, "wards", D.DatasetInfo("wards", "", "", 3, ("a", "b", "c"), ("own",),
                                                          in_all=False, splits=()))
    raw = {**yaml.safe_load(open(CONFIG)), "name": "wards", "dataset": "wards", "partition": "own",
           "model": "cnn-small", "eval": {"every": 0}}
    p = tmp_path / "wards.yaml"
    p.write_text(yaml.safe_dump(raw))
    cfg = load_config(p)
    assert cfg.data_variant == "wards-own" and cfg.num_classes == 3 and manifest(cfg) == (None, None)
    p.write_text(yaml.safe_dump({**raw, "eval": {"every": 1}}))
    with pytest.raises(ValueError, match="hospital-owned"):
        load_config(p)

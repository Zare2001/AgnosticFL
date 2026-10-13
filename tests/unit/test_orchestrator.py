"""Unit tests for FedAvg, run config loading and the equivalence check (no network, no dataset)."""

import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest
from flhub_core.weights import to_safetensors

from hub.orchestrator.config import load_config
from hub.orchestrator.fedavg import fedavg

REPO = Path(__file__).resolve().parents[2]


def w(value, shape=(2, 3)):
    return {"a": np.full(shape, value, dtype=np.float32), "b": np.full(4, -value, dtype=np.float32)}


def test_fedavg_weighted_mean():
    out = fedavg([("s1", w(1.0), 1), ("s2", w(4.0), 3)])
    assert out["a"].dtype == np.float32 and np.allclose(out["a"], 3.25) and np.allclose(out["b"], -3.25)


def test_fedavg_order_independent_and_identity():
    rng = np.random.RandomState(0)
    ups = [(f"s{i}", {"a": rng.randn(50).astype(np.float32)}, int(n)) for i, n in enumerate([785, 784, 785, 3])]
    a = fedavg(ups)
    b = fedavg(list(reversed(ups)))
    assert np.array_equal(a["a"], b["a"])  # bit-identical regardless of arrival order
    single = fedavg([ups[0]])
    assert np.array_equal(single["a"], ups[0][1]["a"])


def test_fedavg_rejects_bad_input():
    with pytest.raises(ValueError):
        fedavg([])
    with pytest.raises(ValueError):
        fedavg([("s", w(1.0), 0)])
    with pytest.raises(ValueError):
        fedavg([("s", w(1.0), 1), ("s", w(2.0), 1)])


def test_load_config(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("name: t\nrounds: 2\ntrain_config: {lr: 0.1}\n"
                 "adapters:\n  - {name: x, url: 'http://127.0.0.1:1', sites: [a, b]}\n"
                 "  - {name: y, url: 'http://127.0.0.1:2', sites: [c]}\nwandb: {project: P, tags: [dev]}\n")
    cfg = load_config(p, rounds=5)
    assert cfg.rounds == 5 and cfg.sites == ["a", "b", "c"] and cfg.train_config["lr"] == 0.1
    assert cfg.train_config["batch_size"] == 64 and cfg.wandb_project == "P" and cfg.tags == ["dev"]
    p.write_text("name: t\nadapters:\n  - {name: x, url: u, sites: [a]}\n  - {name: y, url: v, sites: [a]}\n")
    with pytest.raises(ValueError):
        load_config(p)


def fake_run(root: Path, name: str, deltas: list[float], accs: list[float]) -> Path:
    run = root / name
    (run / "rounds").mkdir(parents=True)
    for r, d in enumerate(deltas):
        (run / "rounds" / f"r{r:04d}.safetensors").write_bytes(to_safetensors(w(1.0 + d)))
    (run / "metrics.jsonl").write_text("".join(json.dumps({"step": r, "global/test_acc": a}) + "\n"
                                               for r, a in enumerate(accs)))
    return run


def test_equivalence_check(tmp_path):
    spec = importlib.util.spec_from_file_location("eq", REPO / "scripts" / "equivalence_check.py")
    eq = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(eq)
    a = fake_run(tmp_path, "a", [0, 0, 0], [0.5, 0.6, 0.7])
    b = fake_run(tmp_path, "b", [0, 0, 0], [0.5, 0.6, 0.7])
    c = fake_run(tmp_path, "c", [0, 0, 1e-3], [0.5, 0.6, 0.7])
    d = fake_run(tmp_path, "d", [0, 0, 0], [0.5, 0.6, 0.69])
    assert eq.compare(str(a), str(b), 1e-5, 0.1)["pass"]
    res = eq.compare(str(a), str(c), 1e-5, 0.1)
    assert not res["pass"] and res["per_round"][2]["max_abs_diff"] == pytest.approx(1e-3, rel=1e-3)
    assert not eq.compare(str(a), str(d), 1e-5, 0.1)["metric_pass"]
    assert eq.main([str(a), str(b), "--json", str(tmp_path / "eq.json")]) == 0

"""Orchestrator and sim v2 end to end with the mock adapter: INV-1 for aggregators, sampling,
min_sites (partial collect + per-site cancel), strict retries, resume and the replay spot-check."""

import json

import pytest
import yaml

from adapters.common import HubClient
from adapters.mock.service import MockAdapter
from hub.core.server import HubCore
from hub.orchestrator.__main__ import main as orch_main
from hub.orchestrator.checkpoint import RunDir
from hub.orchestrator.config import load_config
from hub.orchestrator.executors import RoundFailed
from hub.orchestrator.inv1 import compare_runs
from hub.sim.__main__ import main as sim_main

SITES = ["nvf-h1", "v6-h3", "flw-h5"]


@pytest.fixture
def env(tmp_path):
    hub_srv = HubCore(tmp_path / "hub").build_server("127.0.0.1", 0).start()
    servers = []

    def adapter(**kw):
        srv = MockAdapter(SITES, None, HubClient(hub_srv.url), **kw).build_server("127.0.0.1", 0).start()
        servers.append(srv)
        topo = tmp_path / "topology.yaml"
        topo.write_text(yaml.safe_dump({"hub_url": hub_srv.url, "adapters": {"mock": {"url": srv.url, "sites": SITES}}}))
        return topo

    def config(name, topo, **extra):
        raw = {"schema": 2, "name": name, "rounds": 3, "sites": SITES, "topology": str(topo),
               "train_config": {"local_epochs": 1, "batch_size": 64, "lr": 0.05, "momentum": 0.0},
               "runs_dir": str(tmp_path / "runs"), "deadline_s": 60, "online_wait_s": 20, **extra}
        p = tmp_path / f"{name}.yaml"
        p.write_text(yaml.safe_dump(raw))
        return str(p)

    yield adapter, config, tmp_path
    for srv in servers:
        srv.stop()
    hub_srv.stop()


def latest(tmp_path, name):
    return RunDir(tmp_path / "runs" / f"{name}-latest")


def test_fedadam_real_equals_sim_and_replay(env):
    adapter, config, tmp = env
    cfg = config("adam", adapter(), aggregator={"name": "fedadam", "params": {"eta": 0.01}})
    assert orch_main(["run", "--config", cfg]) == 0
    real = latest(tmp, "adam")
    assert sim_main(["--config", cfg, "--compare", str(real.path), "--name", "adam-sim"]) == 0
    res = json.loads((real.path / "inv1.json").read_text())
    assert res["pass"] and res["hashes_equal"] and res["rounds"] == 3
    assert real.record(3)["agg_state_ref"] == latest(tmp, "adam-sim").record(3)["agg_state_ref"]
    m = real.metrics()
    assert [r["step"] for r in m] == [0, 1, 2, 3] and "site/v6-h3/train_time_s" in m[1] and "site/v6-h3/overhead_s" in m[1]
    assert m[1]["global/test_auc"] > 0 and "adapter/mock/fanout_s" in m[1]
    assert sim_main(["--replay", str(real.path), "--rounds", "1,last"]) == 0


def test_sampling_and_participants_from(env):
    adapter, config, tmp = env
    cfg = config("half", adapter(), participation={"fraction": 0.5, "seed": 7, "min_sites": None})
    assert orch_main(["run", "--config", cfg]) == 0
    real = latest(tmp, "half")
    sets = [real.record(r)["participants"] for r in (1, 2, 3)]
    assert all(len(p) == 2 for p in sets)
    assert sim_main(["--config", cfg, "--compare", str(real.path)]) == 0


def test_min_sites_drops_a_hanging_site(env):
    adapter, config, tmp = env
    cfg = config("minsites", adapter(hang={"v6-h3"}), deadline_s=8, rounds=2,
                 participation={"fraction": 1.0, "seed": 1, "min_sites": 2})
    assert orch_main(["run", "--config", cfg]) == 0
    real = latest(tmp, "minsites")
    rec = real.record(1)
    assert sorted(rec["sites"]) == ["flw-h5", "nvf-h1"] and "v6-h3" in rec["dropped"]
    assert sim_main(["--config", cfg, "--compare", str(real.path)]) == 0


def test_strict_failure_is_retried_then_fails(env):
    adapter, config, tmp = env
    cfg = config("strict", adapter(fail={"flw-h5"}), round_retries=1, rounds=1, online_wait_s=5)
    assert orch_main(["run", "--config", cfg]) == 1
    events = [e["event"] for e in latest(tmp, "strict").events()]
    assert events.count("round_failed") == 2 and "round_retry" in events


def test_resume_is_bit_exact(env, monkeypatch):
    adapter, config, tmp = env
    topo = adapter()
    cfg = config("full", topo, aggregator={"name": "fedavgm", "params": {"eta": 0.5}}, rounds=4)
    assert orch_main(["run", "--config", cfg]) == 0
    from hub.orchestrator import executors

    real_run_round = executors.AdapterExecutor.run_round

    def crash_at_3(self, rnd, *a, **kw):
        if rnd == 3:
            raise KeyboardInterrupt("simulated SIGKILL")
        return real_run_round(self, rnd, *a, **kw)

    cfg2 = config("crash", topo, aggregator={"name": "fedavgm", "params": {"eta": 0.5}}, rounds=4)
    monkeypatch.setattr(executors.AdapterExecutor, "run_round", crash_at_3)
    with pytest.raises(KeyboardInterrupt):
        orch_main(["run", "--config", cfg2])
    monkeypatch.setattr(executors.AdapterExecutor, "run_round", real_run_round)
    crashed = latest(tmp, "crash")
    assert crashed.last_complete_round() == 2
    assert orch_main(["run", "--resume", str(crashed.path)]) == 0
    assert crashed.record(4)["global_ref"] == latest(tmp, "full").record(4)["global_ref"]
    assert crashed.record(4)["agg_state_ref"] == latest(tmp, "full").record(4)["agg_state_ref"]
    assert [r["step"] for r in crashed.metrics()] == [0, 1, 2, 3, 4]
    assert compare_runs(latest(tmp, "full"), crashed)["hashes_equal"]


def test_schema2_train_config_is_flat(env):
    adapter, config, tmp = env
    cfg = load_config(config("tc", adapter(), dataset="organamnist", partition="dir0.3", model="cnn-medium",
                             aggregator={"name": "fedprox", "params": {"mu": 0.01}}, seed=2027))
    assert cfg.train_config == {"local_epochs": 1, "batch_size": 64, "lr": 0.05, "momentum": 0.0, "seed": 2027,
                                "model": "cnn-medium", "data_variant": "organamnist-dir0.3", "prox_mu": 0.01}
    assert cfg.with_seed(2028).train_config["seed"] == 2028 and cfg.with_seed(2028).seed == 2028
    with pytest.raises(RoundFailed):
        raise RoundFailed("x")


def test_hold_file_pauses_at_a_round_boundary(env):
    import threading
    import time

    adapter, config, tmp = env
    cfg = config("hold", adapter(), rounds=2)
    done = threading.Event()

    def release():
        link = tmp / "runs" / "hold-latest"
        while not (link.exists() and (link / "rounds" / "r0000.json").exists()):
            time.sleep(0.05)
        flag = link.resolve() / "hold-after-r0001"
        flag.touch()
        while not (link / "rounds" / "r0001.json").exists():
            time.sleep(0.05)
        time.sleep(1.5)
        assert not (link / "rounds" / "r0002.json").exists()  # held
        flag.unlink()
        done.set()

    t = threading.Thread(target=release, daemon=True)
    t.start()
    assert orch_main(["run", "--config", cfg]) == 0
    assert done.is_set()
    events = [e["event"] for e in latest(tmp, "hold").events()]
    assert "hold" in events and "hold_released" in events


def test_reference_modes_centralized_and_local_with_workers(env):
    _, config, tmp = env
    import yaml

    cfg = config("ref", tmp / "topology.yaml", rounds=1, eval={"site_shaped": False})
    # the topology file must exist for schema 2; reuse the mock adapter's one
    (tmp / "topology.yaml").write_text(yaml.safe_dump({"adapters": {"mock": {"url": "http://127.0.0.1:1",
                                                                             "sites": SITES}}}))
    assert sim_main(["--config", cfg, "--mode", "centralized", "--name", "ref-cen"]) == 0
    assert sim_main(["--config", cfg, "--mode", "local", "--workers", "2", "--name", "ref-local"]) == 0
    rows = latest(tmp, "ref-local").metrics()
    assert rows and all(f"local/{s}/test_acc" in rows[-1] for s in SITES)

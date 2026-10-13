"""Flower adapter + relay ServerApp, without a SuperLink.

A FakeGrid delivers the relay's messages straight to the real ClientApp handlers
(clients/flower/flhub_flower/client_app.py), so these tests run the same training code
as a SuperNode would, on tiny synthetic site files, through the real adapter service
(adapters.flower.service on adapters.common.RelayAdapter) and an in-process hub core.
"""

from __future__ import annotations

import sys
import threading
import time
import uuid
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "clients" / "flower"))
pytest.importorskip("flwr")

from flwr.app import Context, RecordDict  # noqa: E402
from flwr.supercore.task_identity import TaskIdentity  # noqa: E402

from adapters.common import AdapterClient, HubClient, RelayClient, RpcError  # noqa: E402
from adapters.flower.service import FlowerAdapter, stage_fab  # noqa: E402
from flhub_core import data as D  # noqa: E402
from flhub_core.model import init_weights  # noqa: E402
from flhub_core.train import train as flhub_train  # noqa: E402
from flhub_core.weights import max_abs_diff  # noqa: E402
from flhub_flower import client_app, server_app  # noqa: E402
from hub.core.server import HubCore  # noqa: E402

TaskIdentity.run_id, TaskIdentity.node_id, TaskIdentity.task_id = 1, 1, 1
TC = {"local_epochs": 1, "batch_size": 16, "lr": 0.05, "momentum": 0.0, "seed": 2026}


class FakeGrid:
    """Grid whose nodes answer at once with the real ClientApp handlers.

    nodes: node_id -> Context (alive) or None (listed online but never answers).
    """

    def __init__(self, nodes: dict[int, Context | None]) -> None:
        self.nodes = nodes
        self.inbox: dict[str, object] = {}
        self.lock = threading.Lock()

    def get_node_ids(self):
        with self.lock:
            return list(self.nodes)

    def push_messages(self, msgs):
        ids = []
        with self.lock:
            for m in msgs:
                mid = uuid.uuid4().hex
                m.metadata.__dict__["_message_id"] = mid
                self.inbox[mid] = m
                ids.append(mid)
        return ids

    def pull_messages(self, ids):
        out = []
        with self.lock:
            for mid in ids:
                m = self.inbox.get(mid)
                ctx = None if m is None else self.nodes.get(m.metadata.dst_node_id)
                if ctx is None:
                    continue
                del self.inbox[mid]
                handler = {"query": client_app.query, "train": client_app.train}[m.metadata.message_type]
                out.append(handler(m, ctx))
        return out


def site_csv(tmp_path: Path, site: str, seed: int) -> Path:
    rng = np.random.RandomState(seed)
    path = tmp_path / f"{site}.csv"
    path.write_bytes(D.to_csv_bytes(rng.randint(0, 256, size=(40, D.NUM_FEATURES)), rng.randint(0, 2, size=40)))
    return path


def node(site: str, path: Path, node_id: int) -> Context:
    return Context(run_id=1, node_id=node_id, node_config={"site-id": site, "data-path": str(path)},
                   state=RecordDict(), run_config={})


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """In-process hub core + Flower adapter + a relay factory (relays run in threads)."""
    monkeypatch.setattr(server_app, "NODE_REFRESH_S", 0.2)
    monkeypatch.setattr(server_app, "PULL_INTERVAL_S", 0.05)
    monkeypatch.setattr(server_app, "HEARTBEAT_S", 0.2)
    monkeypatch.setattr(server_app, "BEAT_S", 0.2)
    monkeypatch.setattr(server_app, "SITE_WAIT_S", 2.0)
    hub_srv = HubCore(tmp_path / "hub").build_server("127.0.0.1", 0).start()
    hub = HubClient(hub_srv.url)
    adapter = FlowerAdapter(["flw-h5", "flw-h6"], hub)
    srv = adapter.build_server("127.0.0.1", 0).start()
    relays = []

    def start_relay(grid, fab_id=""):
        relay = server_app.Relay(grid, RelayClient(srv.url, hub_srv.url), poll_timeout_s=0.5, query_timeout_s=5.0,
                                 fab_id=fab_id)
        t = threading.Thread(target=relay.serve, daemon=True)
        t.start()
        relays.append(t)

    yield {"adapter": adapter, "client": AdapterClient(srv.url), "hub": hub, "start_relay": start_relay,
           "tmp": tmp_path}
    for t in relays:
        adapter.request_relay_shutdown()
        t.join(timeout=5)
    srv.stop()
    hub_srv.stop()


def wait_state(client, task_id, timeout=60.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        st = client.get_task(task_id)
        if st["state"] in ("completed", "failed", "cancelled"):
            return st
        time.sleep(0.05)
    raise AssertionError(f"task {task_id} still {st}")


def wait_online(client, sites, timeout=20.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        status = {s["site_id"]: s["status"] for s in client.discover()["sites"]}
        if all(status.get(s) == "online" for s in sites):
            return status
        time.sleep(0.05)
    raise AssertionError(f"sites not online: {status}")


def test_round_is_bit_exact_vs_flhub_core(env):
    client, hub = env["client"], env["hub"]
    p5, p6 = site_csv(env["tmp"], "flw-h5", 1), site_csv(env["tmp"], "flw-h6", 2)
    env["start_relay"](FakeGrid({11: node("flw-h5", p5, 11), 12: node("flw-h6", p6, 12)}))
    wait_online(client, ["flw-h5", "flw-h6"])
    assert client.discover()["adapter"] == "flower"
    w0 = init_weights(7)
    ref = hub.put_weights(w0)
    tid = client.dispatch(3, {"ref": ref}, TC, ["flw-h5", "flw-h6"], deadline_s=60)
    assert wait_state(client, tid)["state"] == "completed"
    envs = client.collect(tid)
    assert [e["site_id"] for e in envs] == ["flw-h5", "flw-h6"]
    for e, path in zip(envs, (p5, p6)):
        want, n, metrics = flhub_train(w0, D.load_site(path), {**TC, "round": 3})
        got = hub.get_weights(e["weights"])
        assert list(got) == list(want)
        assert max_abs_diff(got, want) == 0.0
        assert e["num_examples"] == n == 40
        assert e["metrics"]["train_loss"] == pytest.approx(metrics["train_loss"], abs=1e-12)
        assert e["base_model"] == ref and e["framework"] == "flower" and e["round"] == 3
        relay_keys = ("flower_relay_prep_s", "flower_relay_push_s", "flower_relay_reply_s", "flower_relay_store_s")
        assert all(e["metrics"][k] >= 0 for k in relay_keys), e["metrics"]


def test_restarted_supernode_replaces_stale_node(env):
    client = env["client"]
    p5, p6 = site_csv(env["tmp"], "flw-h5", 1), site_csv(env["tmp"], "flw-h6", 2)
    # node 5 is the dead predecessor of node 15: still listed online, never answers
    env["start_relay"](FakeGrid({5: None, 15: node("flw-h5", p5, 15), 16: node("flw-h6", p6, 16)}))
    wait_online(client, ["flw-h5", "flw-h6"])
    tid = client.dispatch(1, {"ref": env["hub"].put_weights(init_weights(1))}, TC, ["flw-h5"], deadline_s=60)
    assert wait_state(client, tid)["state"] == "completed"


def test_missing_and_lost_sites_fail_fast(env):
    client = env["client"]
    grid = FakeGrid({21: node("flw-h5", site_csv(env["tmp"], "flw-h5", 1), 21)})
    env["start_relay"](grid)
    wait_online(client, ["flw-h5"])
    assert {s["site_id"]: s["status"] for s in client.discover()["sites"]}["flw-h6"] == "offline"
    ref = env["hub"].put_weights(init_weights(1))
    tid = client.dispatch(1, {"ref": ref}, TC, ["flw-h5", "flw-h6"], deadline_s=60)
    st = wait_state(client, tid, timeout=50)
    assert st["state"] == "failed" and "flw-h6" in st["error"], st
    with pytest.raises(RpcError) as err:
        client.collect(tid)
    assert err.value.code == -32013
    # flw-h5's node hangs, then disappears: the round fails as soon as it is gone
    grid.nodes[21] = None
    tid = client.dispatch(2, {"ref": ref}, TC, ["flw-h5"], deadline_s=60)
    time.sleep(0.5)
    with grid.lock:
        del grid.nodes[21]
    st = wait_state(client, tid, timeout=20)
    assert st["state"] == "failed" and "went offline" in st["error"], st


def test_cancel_then_next_round(env):
    client = env["client"]
    p5 = site_csv(env["tmp"], "flw-h5", 1)
    grid = FakeGrid({31: None})  # hangs until replaced
    env["start_relay"](grid)
    ref = env["hub"].put_weights(init_weights(1))
    with grid.lock:
        grid.nodes[31] = node("flw-h5", p5, 31)
    wait_online(client, ["flw-h5"])
    with grid.lock:
        grid.nodes[31] = None  # the next round hangs at the site
    tid = client.dispatch(1, {"ref": ref}, TC, ["flw-h5"], deadline_s=60)
    time.sleep(0.5)
    assert client.cancel(tid)["state"] == "cancelled"
    with grid.lock:
        grid.nodes[31] = node("flw-h5", p5, 31)
    tid = client.dispatch(2, {"ref": ref}, TC, ["flw-h5"], deadline_s=60)
    assert wait_state(client, tid)["state"] == "completed"


def test_relay_liveness_and_shutdown(env):
    adapter = env["adapter"]
    assert not adapter.relay_alive()
    assert {s["status"] for s in env["client"].discover()["sites"]} == {"unknown"}
    env["start_relay"](FakeGrid({}))
    end = time.monotonic() + 5
    while not adapter.relay_alive() and time.monotonic() < end:
        time.sleep(0.05)
    assert adapter.relay_alive()
    adapter.request_relay_shutdown()
    while not adapter.shutdown_delivered() and time.monotonic() < end + 5:
        time.sleep(0.05)
    assert adapter.shutdown_delivered()


def test_lost_relay_fails_its_round_at_once(env):
    client, adapter = env["client"], env["adapter"]
    p5 = site_csv(env["tmp"], "flw-h5", 1)
    grid = FakeGrid({41: node("flw-h5", p5, 41)})
    env["start_relay"](grid)
    wait_online(client, ["flw-h5"])
    with grid.lock:
        grid.nodes[41] = None  # the round hangs at the site, as if the relay had died with it
    tid = client.dispatch(1, {"ref": env["hub"].put_weights(init_weights(1))}, TC, ["flw-h5"], deadline_s=60)
    end = time.monotonic() + 10
    while tid not in adapter._taken and time.monotonic() < end:
        time.sleep(0.05)
    assert adapter.fail_taken_jobs("flower relay run lost") == [tid]
    st = wait_state(client, tid, timeout=10)
    assert st["state"] == "failed" and "relay run lost" in st["error"], st
    assert adapter.fail_taken_jobs("again") == []


def test_relay_with_another_fab_is_asked_to_leave(env):
    adapter = env["adapter"]
    adapter.fab_id = "fab-new"
    env["start_relay"](FakeGrid({}), fab_id="fab-old")
    end = time.monotonic() + 10
    while adapter.relay_info.get("fab_id") != "fab-old" and time.monotonic() < end:
        time.sleep(0.05)
    assert adapter.relay_info["fab_id"] == "fab-old"
    while not adapter.shutdown_delivered() and time.monotonic() < end:
        time.sleep(0.05)
    assert adapter.shutdown_delivered()  # the stale relay took its shutdown


def test_stage_fab_ships_flhub_core(tmp_path):
    app, core = REPO / "clients" / "flower", REPO / "flhub_core" / "src" / "flhub_core"
    out = tmp_path / "fab-src"
    fab_id = stage_fab(app, core, out)
    assert (out / "pyproject.toml").exists() and (out / "flhub_flower" / "client_app.py").exists()
    shipped = sorted(p.name for p in (out / "flhub_core").glob("*.py"))
    assert shipped == sorted(p.name for p in core.glob("*.py"))
    assert not list(out.rglob("__pycache__")) and not list(out.rglob("*.pyc"))
    assert stage_fab(app, core, out) == fab_id  # same sources, same id


def test_partial_collect_site_cancel_then_next_round(env):
    """flhub/0.2: a done site is collectable while another hangs; cancelling the hanging site
    ends the task, and the next dispatch to both sites is accepted at once."""
    client, adapter = env["client"], env["adapter"]
    p5, p6 = site_csv(env["tmp"], "flw-h5", 1), site_csv(env["tmp"], "flw-h6", 2)
    grid = FakeGrid({51: node("flw-h5", p5, 51), 52: node("flw-h6", p6, 52)})
    env["start_relay"](grid)
    wait_online(client, ["flw-h5", "flw-h6"])
    with grid.lock:
        grid.nodes[52] = None  # flw-h6 straggles: never answers this round
    ref = env["hub"].put_weights(init_weights(1))
    tid = client.dispatch(1, {"ref": ref}, TC, ["flw-h5", "flw-h6"], deadline_s=60)
    end = time.monotonic() + 20
    while client.get_task(tid)["sites"]["flw-h5"] != "done" and time.monotonic() < end:
        time.sleep(0.05)
    part = client.collect_partial(tid)
    assert part["state"] == "running" and [e["site_id"] for e in part["envelopes"]] == ["flw-h5"]
    st = client.cancel(tid, ["flw-h6"])
    assert st["sites"] == {"flw-h5": "done", "flw-h6": "cancelled"} and st["state"] == "cancelled"
    with grid.lock:
        grid.nodes[52] = node("flw-h6", p6, 52)
    tid2 = client.dispatch(2, {"ref": ref}, TC, ["flw-h5", "flw-h6"], deadline_s=60)  # no -32015
    st2 = wait_state(client, tid2)
    assert st2["state"] == "completed" and st2["sites"] == {"flw-h5": "done", "flw-h6": "done"}, st2
    assert adapter.tasks.get(tid).site_state["flw-h6"] == "cancelled"  # late reply was dropped


def test_missing_site_fails_alone(env):
    """flhub/0.2: a site without a SuperNode fails on its own; the other site's result stays."""
    client = env["client"]
    env["start_relay"](FakeGrid({61: node("flw-h5", site_csv(env["tmp"], "flw-h5", 1), 61)}))
    wait_online(client, ["flw-h5"])
    tid = client.dispatch(1, {"ref": env["hub"].put_weights(init_weights(1))}, TC, ["flw-h5", "flw-h6"],
                          deadline_s=60)
    st = wait_state(client, tid, timeout=30)
    assert st["state"] == "failed" and st["sites"] == {"flw-h5": "done", "flw-h6": "failed"}, st
    assert "no connected SuperNode" in st["site_errors"]["flw-h6"]
    assert [e["site_id"] for e in client.collect_partial(tid)["envelopes"]] == ["flw-h5"]

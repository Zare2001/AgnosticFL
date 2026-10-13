"""Unit tests for adapters/vantage6 with a fake vantage6 client (no vantage6 server needed)."""

import base64
import json
import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest
from flhub_core.weights import from_safetensors, sha256_ref, to_b64

from adapters.common import AdapterClient, HubClient
from adapters.vantage6 import service
from hub.core.server import HubCore

TC = {"local_epochs": 1, "batch_size": 64, "lr": 0.05, "momentum": 0.0, "seed": 2026}
ORGS = {"v6-h3": 2, "v6-h4": 3}


class FakeV6:
    """Just the parts of vantage6.client.UserClient the adapter uses. One vantage6 task per site:
    `final` maps an organisation id to its final status (default completed) after `polls` polls."""

    def __init__(self, final=None, polls=2, tamper=None, down_polls=0):
        self.final, self.polls_needed, self.tamper = final or {}, polls, tamper
        self.down_polls = down_polls  # the first status polls fail (server restart)
        self.lock = threading.Lock()
        self.created, self.killed, self.deleted, self.polls = {}, [], [], {}
        self.task = SimpleNamespace(create=self._create, kill=self._kill, delete=self.deleted.append)
        self.check_if_blob_store_enabled = lambda: False
        self.result = SimpleNamespace(from_task=lambda id_: {"data": self._results(id_)})

    def _create(self, **kw):
        with self.lock:
            id_ = 10 + len(self.created)
            self.created[id_] = kw
        return {"id": id_}

    def _kill(self, id_):
        self.killed.append(id_)
        return {"msg": "killed"}

    def org(self, id_):
        (org,) = self.created[id_]["organizations"]
        return org

    def status(self, id_):
        if id_ in self.killed:
            return "killed by user"
        if self.polls.get(id_, 0) < self.polls_needed:
            return "active"
        return self.final.get(self.org(id_), "completed")

    def request(self, path, params=None, attempts_on_timeout=None):
        if path == "node":
            return {"data": [{"organization": {"id": 1}, "status": None},
                             {"organization": {"id": 2}, "status": "online"},
                             {"organization": {"id": 3}, "status": "offline"}]}
        if path == "run":
            id_ = params["task_id"]
            return {"data": [{"id": 100 + id_, "status": self.status(id_), "input": "x" * 10,
                              "log": f"Traceback ... ValueError: boom at org {self.org(id_)}"}]}
        id_ = int(path.split("/")[1])
        assert path == f"task/{id_}/status"
        with self.lock:
            if self.down_polls:  # what the client returns after attempts_on_timeout connection errors
                self.down_polls -= 1
                return {"msg": "Connection error"}
            self.polls[id_] = self.polls.get(id_, 0) + 1
        return {"status": self.status(id_)}

    def _results(self, id_):
        kwargs = self.created[id_]["input_"]["kwargs"]
        raw = base64.b64decode(kwargs["weights_b64"])
        ((org_id, site),) = kwargs["sites"].items()
        w = {k: v + np.float32(1) for k, v in from_safetensors(raw).items()}
        res = {"weights_b64": to_b64(w), "num_examples": 10 * int(org_id), "site_id": site,
               "metrics": {"train_loss": 0.5, "train_acc": 0.75}, "organization_id": int(org_id),
               "base_model": sha256_ref(raw), "client_code_version": "0.1.0+test"}
        res.update(self.tamper or {})
        return [{"run": {"id": 100 + id_}, "result": json.dumps(res)}]

    def task_of(self, site):
        return next(i for i, kw in self.created.items() if kw["organizations"] == [ORGS[site]])


@pytest.fixture
def hub(tmp_path):
    srv = HubCore(tmp_path / "hub").build_server("127.0.0.1", 0).start()
    yield HubClient(srv.url)
    srv.stop()


def start(hub, fake, monkeypatch):
    monkeypatch.setattr(service, "POLL_S", 0.01)
    adapter = service.Vantage6Adapter(list(ORGS), server_url="http://v6", server_port=7601, api_path="/api",
                                      username="hub-adapter", password="x", collaboration="flhub",
                                      image="reg:5050/flhub/v6-train:abc", hub=hub)

    def login():
        adapter.collab_id, adapter.org_ids = 1, dict(ORGS)
        return fake

    adapter._login = login
    srv = adapter.build_server("127.0.0.1", 0).start()
    return adapter, srv, AdapterClient(srv.url)


def wait_state(client, task_id, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        st = client.get_task(task_id)
        if st["state"] in ("completed", "failed", "cancelled"):
            return st
        time.sleep(0.02)
    raise AssertionError("task did not finish")


def wait_created(fake, n, timeout=5):
    end = time.time() + timeout
    while len(fake.created) < n and time.time() < end:
        time.sleep(0.01)
    assert len(fake.created) >= n


def model(hub):
    w = {"conv.weight": np.arange(6, dtype=np.float32).reshape(2, 3), "fc.bias": np.zeros(2, np.float32)}
    return hub.put_weights(w), w


def test_db_label():
    assert service.db_label(None) == "default"
    assert service.db_label("organamnist-dir0.3") == "organamnist_dir0_3"
    assert service.db_label("organamnist-iid12") == "organamnist_iid12"
    assert service.db_label("2d-x") == "v_2d_x"


def test_round_maps_to_one_vantage6_task_per_site(hub, monkeypatch):
    fake = FakeV6()
    adapter, srv, client = start(hub, fake, monkeypatch)
    try:
        assert {s["site_id"]: s["status"] for s in client.discover()["sites"]} == {"v6-h3": "online",
                                                                                  "v6-h4": "offline"}
        ref, w = model(hub)
        task_id = client.dispatch(5, {"ref": ref}, TC, ["v6-h4", "v6-h3"])
        assert wait_state(client, task_id)["state"] == "completed"
        assert sorted(kw["organizations"][0] for kw in fake.created.values()) == [2, 3]
        for kw in fake.created.values():
            assert kw["collaboration"] == 1 and kw["name"] == "flhub-r5"
            assert kw["image"] == "reg:5050/flhub/v6-train:abc" and kw["databases"] == [{"label": "default"}]
            kwargs = kw["input_"]["kwargs"]
            assert kw["input_"]["method"] == "train" and kwargs["train_config"] == {**TC, "round": 5}
            assert kwargs["sites"] == {str(kw["organizations"][0]): {2: "v6-h3", 3: "v6-h4"}[kw["organizations"][0]]}
            assert sha256_ref(base64.b64decode(kwargs["weights_b64"])) == ref  # model bytes sent as-is
        envs = client.collect(task_id)
        assert [e["site_id"] for e in envs] == ["v6-h4", "v6-h3"]
        assert [e["num_examples"] for e in envs] == [30, 20]
        for e in envs:
            assert e["framework"] == "vantage6" and e["base_model"] == ref
            assert e["metrics"]["train_loss"] == 0.5 and e["metrics"]["vantage6_fetch_s"] >= 0
            assert {"vantage6_create_s", "vantage6_store_s"} <= set(e["metrics"])
            got = hub.get_weights(e["weights"])
            assert all(np.array_equal(got[k], w[k] + 1) for k in w)
        assert fake.killed == [] and fake.deleted == []  # small model: tasks kept on the server
    finally:
        srv.stop()


def test_data_variant_selects_the_database_label(hub, monkeypatch):
    fake = FakeV6()
    adapter, srv, client = start(hub, fake, monkeypatch)
    try:
        ref, _ = model(hub)
        task_id = client.call("dispatch", round=1, model={"ref": ref}, train_config={**TC, "data_variant": "organamnist-dir0.3"},
                              sites=list(ORGS), data_variant="organamnist-dir0.3")["task_id"]
        assert wait_state(client, task_id)["state"] == "completed"
        assert [kw["databases"] for kw in fake.created.values()] == [[{"label": "organamnist_dir0_3"}]] * 2
    finally:
        srv.stop()


def test_failed_site_reports_its_log_and_the_other_site_completes(hub, monkeypatch):
    fake = FakeV6(final={2: "crashed"})
    adapter, srv, client = start(hub, fake, monkeypatch)
    try:
        ref, _ = model(hub)
        task_id = client.dispatch(1, {"ref": ref}, TC, list(ORGS))
        st = wait_state(client, task_id)
        assert st["state"] == "failed" and st["sites"] == {"v6-h3": "failed", "v6-h4": "done"}, st
        assert "crashed" in st["site_errors"]["v6-h3"] and "ValueError: boom at org 2" in st["site_errors"]["v6-h3"]
        part = client.call("collect", task_id=task_id, partial=True)
        assert [e["site_id"] for e in part["envelopes"]] == ["v6-h4"]
        assert fake.killed == []  # the healthy site's task is left alone
    finally:
        srv.stop()


def test_cancel_kills_every_vantage6_task(hub, monkeypatch):
    fake = FakeV6(polls=10**9)
    adapter, srv, client = start(hub, fake, monkeypatch)
    try:
        ref, _ = model(hub)
        task_id = client.dispatch(1, {"ref": ref}, TC, list(ORGS))
        wait_created(fake, 2)
        time.sleep(0.05)
        assert client.cancel(task_id)["state"] == "cancelled"
        assert sorted(fake.killed) == sorted(fake.created)
        assert client.get_task(task_id)["state"] == "cancelled"
    finally:
        srv.stop()


def test_site_cancel_kills_only_that_site_then_next_dispatch(hub, monkeypatch):
    fake = FakeV6(polls=20)
    adapter, srv, client = start(hub, fake, monkeypatch)
    try:
        ref, _ = model(hub)
        task_id = client.dispatch(6, {"ref": ref}, TC, list(ORGS))
        wait_created(fake, 2)
        time.sleep(0.05)
        res = client.call("cancel", task_id=task_id, sites=["v6-h4"])
        assert res["sites"]["v6-h4"] == "cancelled"
        assert fake.killed == [fake.task_of("v6-h4")]
        st = wait_state(client, task_id)
        assert st["state"] == "cancelled" and st["sites"] == {"v6-h3": "done", "v6-h4": "cancelled"}, st
        next_id = client.dispatch(7, {"ref": ref}, TC, list(ORGS))  # not -32015
        assert wait_state(client, next_id)["state"] == "completed"
    finally:
        srv.stop()


def test_server_outage_only_delays_the_round(hub, monkeypatch):
    fake = FakeV6(down_polls=6)
    adapter, srv, client = start(hub, fake, monkeypatch)
    try:
        ref, _ = model(hub)
        st = wait_state(client, client.dispatch(2, {"ref": ref}, TC, list(ORGS)))
        assert st["state"] == "completed", st
    finally:
        srv.stop()


def test_large_models_delete_their_vantage6_tasks(hub, monkeypatch):
    monkeypatch.setattr(service, "PURGE_MIN_MB", 0)
    fake = FakeV6()
    adapter, srv, client = start(hub, fake, monkeypatch)
    try:
        ref, _ = model(hub)
        assert wait_state(client, client.dispatch(3, {"ref": ref}, TC, list(ORGS)))["state"] == "completed"
        end = time.time() + 5
        while len(fake.deleted) < 2 and time.time() < end:
            time.sleep(0.01)
        assert sorted(fake.deleted) == sorted(fake.created)
    finally:
        srv.stop()


@pytest.mark.parametrize("tamper, message", [({"base_model": "sha256:" + "0" * 64}, "trained from"),
                                             ({"site_id": "v6-h9"}, "reports site")])
def test_result_checks(hub, monkeypatch, tamper, message):
    fake = FakeV6(tamper=tamper)
    adapter, srv, client = start(hub, fake, monkeypatch)
    try:
        ref, _ = model(hub)
        st = wait_state(client, client.dispatch(1, {"ref": ref}, TC, list(ORGS)))
        assert st["state"] == "failed" and all(message in e for e in st["site_errors"].values()), st
    finally:
        srv.stop()

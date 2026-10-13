"""Unit tests for adapters/vantage6 with a fake vantage6 client (no vantage6 server needed)."""

import base64
import json
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
    """Just the parts of vantage6.client.UserClient the adapter uses."""

    def __init__(self, final="completed", polls_until_final=2, tamper=None):
        self.final, self.polls_until_final, self.tamper = final, polls_until_final, tamper
        self.created, self.killed, self.polls = [], [], 0
        self.task = SimpleNamespace(create=self._create, kill=lambda id_: self.killed.append(id_))
        self.run = SimpleNamespace(from_task=lambda id_: {"data": self._runs()})
        self.result = SimpleNamespace(from_task=lambda id_: {"data": self._results()})
        self.node = SimpleNamespace(list=lambda **kw: {"data": [
            {"organization": {"id": 1}, "status": None},
            {"organization": {"id": 2}, "status": "online"},
            {"organization": {"id": 3}, "status": "offline"}]})

    def _create(self, **kw):
        self.created.append(kw)
        return {"id": 7}

    def request(self, path):
        assert path == "task/7/status"
        self.polls += 1
        return {"status": self.final if self.polls >= self.polls_until_final else "active"}

    def _status(self):
        return self.final if self.polls >= self.polls_until_final else "active"

    def _runs(self):
        orgs = self.created[-1]["organizations"]
        return [{"id": 100 + o, "organization": {"id": o}, "status": self._status(),
                 "log": f"Traceback ... ValueError: boom at org {o}"} for o in orgs]

    def _results(self):
        kwargs = self.created[-1]["input_"]["kwargs"]
        raw = base64.b64decode(kwargs["weights_b64"])
        out = []
        for org_id, site in kwargs["sites"].items():
            w = {k: v + np.float32(1) for k, v in from_safetensors(raw).items()}
            res = {"weights_b64": to_b64(w), "num_examples": 10 * int(org_id), "site_id": site,
                   "metrics": {"train_loss": 0.5, "train_acc": 0.75}, "organization_id": int(org_id),
                   "base_model": sha256_ref(raw), "client_code_version": "0.1.0+test"}
            if self.tamper:
                res.update(self.tamper)
            out.append({"run": {"id": 100 + int(org_id)}, "result": json.dumps(res)})
        return out


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


def model(hub):
    w = {"conv.weight": np.arange(6, dtype=np.float32).reshape(2, 3), "fc.bias": np.zeros(2, np.float32)}
    return hub.put_weights(w), w


def test_round_maps_to_one_vantage6_task(hub, monkeypatch):
    fake = FakeV6()
    adapter, srv, client = start(hub, fake, monkeypatch)
    try:
        assert {s["site_id"]: s["status"] for s in client.discover()["sites"]} == {"v6-h3": "online",
                                                                                  "v6-h4": "offline"}
        ref, w = model(hub)
        task_id = client.dispatch(5, {"ref": ref}, TC, ["v6-h4", "v6-h3"])
        assert wait_state(client, task_id)["state"] == "completed"
        created = fake.created[0]
        assert created["organizations"] == [3, 2] and created["collaboration"] == 1
        assert created["image"] == "reg:5050/flhub/v6-train:abc" and created["databases"] == [{"label": "default"}]
        kwargs = created["input_"]["kwargs"]
        assert created["input_"]["method"] == "train" and kwargs["train_config"] == {**TC, "round": 5}
        assert sha256_ref(base64.b64decode(kwargs["weights_b64"])) == ref  # model bytes sent as-is
        envs = client.collect(task_id)
        assert [e["site_id"] for e in envs] == ["v6-h4", "v6-h3"]
        assert [e["num_examples"] for e in envs] == [30, 20]
        for e in envs:
            assert e["framework"] == "vantage6" and e["base_model"] == ref
            got = hub.get_weights(e["weights"])
            assert all(np.array_equal(got[k], w[k] + 1) for k in w)
    finally:
        srv.stop()


def test_failed_run_reports_framework_log(hub, monkeypatch):
    fake = FakeV6(final="crashed")
    adapter, srv, client = start(hub, fake, monkeypatch)
    try:
        ref, _ = model(hub)
        st = wait_state(client, client.dispatch(1, {"ref": ref}, TC, list(ORGS)))
        assert st["state"] == "failed"
        assert "crashed" in st["error"] and "ValueError: boom" in st["error"] and "v6-h3" in st["error"]
        assert fake.killed == [7]
    finally:
        srv.stop()


def test_cancel_kills_vantage6_task(hub, monkeypatch):
    fake = FakeV6(polls_until_final=10**9)
    adapter, srv, client = start(hub, fake, monkeypatch)
    try:
        ref, _ = model(hub)
        task_id = client.dispatch(1, {"ref": ref}, TC, list(ORGS))
        end = time.time() + 5
        while not fake.created and time.time() < end:
            time.sleep(0.01)
        assert client.cancel(task_id)["state"] == "cancelled"
        assert fake.killed == [7]
        assert client.get_task(task_id)["state"] == "cancelled"
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
        assert st["state"] == "failed" and message in st["error"]
    finally:
        srv.stop()

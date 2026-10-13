"""Unit tests for adapters/common, hub core and the relay pattern (no framework needed)."""

import json
import threading
import time

import numpy as np
import pytest
from flhub_core.model import init_weights
from flhub_core.weights import spec, to_safetensors

from adapters.common import (
    AdapterClient,
    BaseAdapter,
    Envelope,
    FrameworkError,
    HubClient,
    RelayAdapter,
    RelayClient,
    RpcError,
    SiteResult,
    envelope_problems,
    train_config_hash,
    weights_problems,
)
from adapters.common.hubclient import http_request
from adapters.common.protocol import client_config
from hub.core.server import HubCore

TC = {"local_epochs": 1, "batch_size": 64, "lr": 0.05, "momentum": 0.0, "seed": 2026}


@pytest.fixture
def hub(tmp_path):
    srv = HubCore(tmp_path / "hub").build_server("127.0.0.1", 0).start()
    yield HubClient(srv.url)
    srv.stop()


def wait_state(client, task_id, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        st = client.get_task(task_id)
        if st["state"] in ("completed", "failed", "cancelled"):
            return st
        time.sleep(0.05)
    raise AssertionError("task did not finish")


class FakeAdapter(BaseAdapter):
    adapter_name = framework = "fake"

    def __init__(self, sites, hub, mode="ok"):
        super().__init__(sites, hub)
        self.mode = mode
        self.seen_config = None

    def site_status(self):
        return {s: "online" for s in self.sites}

    def run_round(self, task, weights, cfg):
        self.seen_config = cfg
        if self.mode == "fail":
            raise FrameworkError("boom from framework")
        if self.mode == "slow":
            while not task.cancelled:
                time.sleep(0.05)
            raise FrameworkError("cancelled")
        return [SiteResult(s, 10 + i, {"train_loss": 0.5}, "v1",
                           weights={k: v + 1 for k, v in weights.items()}) for i, s in enumerate(task.sites)]


def start(adapter):
    srv = adapter.build_server("127.0.0.1", 0).start()
    return srv, AdapterClient(srv.url)


def test_hub_core_blobs_and_spec(hub):
    w = init_weights(0)
    ref = hub.put_weights(w)
    assert hub.has_blob(ref) and not hub.has_blob("sha256:" + "0" * 64)
    assert hub.put_weights(w) == ref  # idempotent
    assert list(hub.get_weights(ref)) == list(w)
    status, _ = http_request(hub.blob_url(ref), "PUT", b"other bytes")
    assert status == 400
    assert hub.get_spec() is None
    hub.put_spec(spec(w))
    assert hub.get_spec() == spec(w)
    assert hub.health()["ok"]


def test_jsonrpc_errors(hub):
    srv, client = start(FakeAdapter(["a"], hub))
    try:
        status, body = http_request(srv.url + "/rpc", "POST", b"{not json")
        assert status == 200 and json.loads(body)["error"]["code"] == -32700
        with pytest.raises(RpcError) as e:
            client.call("nope")
        assert e.value.code == -32601
        with pytest.raises(RpcError) as e:
            client.call("discover", protocol="flhub/9.9")
        assert e.value.code == -32010
        with pytest.raises(RpcError) as e:
            client.get_task("missing")
        assert e.value.code == -32012
    finally:
        srv.stop()


def test_dispatch_collect_envelopes(hub):
    w = init_weights(0)
    ref = hub.put_weights(w)
    adapter = FakeAdapter(["a", "b"], hub)
    srv, client = start(adapter)
    try:
        d = client.discover()
        assert d["protocol"] == "flhub/0.1" and [s["status"] for s in d["sites"]] == ["online", "online"]
        with pytest.raises(RpcError) as e:
            client.dispatch(3, {"ref": ref}, TC, ["a", "zzz"])
        assert e.value.code == -32011
        tid = client.dispatch(3, {"ref": ref, "url": hub.blob_url(ref)}, TC, ["a", "b"])
        assert wait_state(client, tid)["state"] == "completed"
        envs = client.collect(tid)
        assert envs == client.collect(tid)  # idempotent
        assert adapter.seen_config == client_config(TC, 3)
        for env_d in envs:
            env = Envelope.from_dict(env_d)
            assert envelope_problems(env, round=3, base_model=ref, expected_sites=["a", "b"],
                                     train_config_hash=train_config_hash(TC)) == []
            got = hub.get_weights(env.weights)
            assert weights_problems(got, spec(w)) == []
            assert np.allclose(got["fc2.bias"], w["fc2.bias"] + 1)
        assert [e["num_examples"] for e in envs] == [10, 11]
    finally:
        srv.stop()


def test_failure_busy_cancel_and_deadline(hub):
    ref = hub.put_weights(init_weights(0))
    srv, client = start(FakeAdapter(["a"], hub, mode="fail"))
    try:
        tid = client.dispatch(1, {"ref": ref}, TC, ["a"])
        st = wait_state(client, tid)
        assert st["state"] == "failed" and "boom from framework" in st["error"]
        with pytest.raises(RpcError) as e:
            client.collect(tid)
        assert e.value.code == -32013
    finally:
        srv.stop()
    srv, client = start(FakeAdapter(["a"], hub, mode="slow"))
    try:
        tid = client.dispatch(1, {"ref": ref}, TC, ["a"])
        with pytest.raises(RpcError) as e:
            client.dispatch(1, {"ref": ref}, TC, ["a"])
        assert e.value.code == -32015
        assert client.cancel(tid)["state"] == "cancelled"
        assert wait_state(client, tid)["state"] == "cancelled"
        tid = client.dispatch(2, {"ref": ref}, TC, ["a"], deadline_s=0.3)
        st = wait_state(client, tid)
        assert st["state"] == "failed" and "deadline" in st["error"]
    finally:
        srv.stop()


def test_envelope_problems_catch_mismatches():
    env = Envelope(round=2, site_id="x", framework="f", base_model="sha256:" + "a" * 64, weights="nope",
                   num_examples=0, metrics={"l": "bad"}, train_config_hash="sha256:" + "b" * 64,
                   client_code_version="v")
    probs = envelope_problems(env, round=3, base_model="sha256:" + "c" * 64, expected_sites=["y"],
                              train_config_hash=train_config_hash(TC))
    assert len(probs) == 7
    w = init_weights(0)
    w["fc2.bias"] = np.array([np.nan, 0], dtype=np.float32)
    assert weights_problems(w, spec(init_weights(0))) == ["fc2.bias: non-finite values"]


class FakeRelayAdapter(RelayAdapter):
    adapter_name = framework = "fakerelay"


def test_relay_roundtrip(hub):
    w = init_weights(0)
    ref = hub.put_weights(w)
    adapter = FakeRelayAdapter(["s1", "s2"], hub)
    srv, client = start(adapter)
    relay = RelayClient(srv.url, hub.base_url)
    seen = {}

    def relay_loop():
        while True:
            job = relay.next_job(timeout=1)
            if job["type"] == "shutdown":
                return
            if job["type"] != "train":
                continue
            seen["job"] = job
            model = relay.get_model(job)
            if job["round"] == 99:
                relay.post_error(job["job_id"], "framework says no")
                continue
            relay.post_progress(job["job_id"], 1)
            relay.post_result(job["job_id"], [relay.result(s, model, 7, {"train_loss": 1.0}, "v2")
                                              for s in job["sites"]])

    t = threading.Thread(target=relay_loop, daemon=True)
    t.start()
    try:
        assert [s["status"] for s in client.discover()["sites"]] == ["unknown", "unknown"]
        relay.report_sites({"s1": "online"})
        assert [s["status"] for s in client.discover()["sites"]] == ["online", "offline"]
        tid = client.dispatch(5, {"ref": ref}, TC, ["s1", "s2"])
        assert wait_state(client, tid)["state"] == "completed"
        assert seen["job"]["config"] == client_config(TC, 5) and seen["job"]["deadline_s"] > 0
        envs = client.collect(tid)
        assert [e["site_id"] for e in envs] == ["s1", "s2"] and all(e["client_code_version"] == "v2" for e in envs)
        assert all(e["weights"] == ref for e in envs)  # relay echoed the model
        tid = client.dispatch(99, {"ref": ref}, TC, ["s1"])
        st = wait_state(client, tid)
        assert st["state"] == "failed" and "framework says no" in st["error"]
        http_request(srv.url + "/relay/shutdown", "POST", b"{}")
        t.join(timeout=5)
        assert not t.is_alive()
    finally:
        srv.stop()

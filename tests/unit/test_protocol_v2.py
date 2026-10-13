"""flhub/0.2 in adapters/common: per-site progress, partial collect, per-site cancel, relay site posts."""

import threading
import time

import pytest
from flhub_core.model import init_weights

from adapters.common import AdapterClient, BaseAdapter, Envelope, HubClient, RelayAdapter, RelayClient, RpcError
from adapters.common import SiteResult, envelope_problems
from adapters.common.hubclient import http_request
from hub.core.server import HubCore

TC = {"local_epochs": 1, "batch_size": 64, "lr": 0.05, "momentum": 0.0, "seed": 2026}


@pytest.fixture
def hub(tmp_path):
    srv = HubCore(tmp_path / "hub").build_server("127.0.0.1", 0).start()
    yield HubClient(srv.url)
    srv.stop()


class StepAdapter(BaseAdapter):
    """Reports each site when its event is set; a site whose event never fires stays running."""

    adapter_name = framework = "step"

    def __init__(self, sites, hub):
        super().__init__(sites, hub)
        self.go = {s: threading.Event() for s in sites}
        self.cancel_calls = []

    def site_status(self):
        return {s: "online" for s in self.sites}

    def on_cancel_sites(self, task, sites):
        self.cancel_calls.append(list(sites))

    def run_round(self, task, weights, cfg):
        left = list(task.sites)
        while left and not task.cancelled:
            for s in list(left):
                if task.site_cancelled(s):
                    left.remove(s)
                elif self.go[s].is_set():
                    self.report_site(task, SiteResult(s, 5, {"train_loss": 1.0}, "v", weights=weights))
                    left.remove(s)
            time.sleep(0.01)
        return []


def start(adapter):
    srv = adapter.build_server("127.0.0.1", 0).start()
    return srv, AdapterClient(srv.url)


def wait_for(fn, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        value = fn()
        if value:
            return value
        time.sleep(0.02)
    raise AssertionError("condition not met")


def test_discover_advertises_v2(hub):
    srv, client = start(StepAdapter(["a"], hub))
    try:
        d = client.negotiate()
        assert d["protocol"] == "flhub/0.2" and client.protocol == "flhub/0.2"
        assert d["protocols"] == ["flhub/0.1", "flhub/0.2"]
        caps = d["capabilities"]
        assert caps["max_payload_mb"] >= 1024 and caps["partial_collect"] and caps["site_cancel"]
    finally:
        srv.stop()


def test_partial_collect_site_cancel_then_next_dispatch(hub):
    ref = hub.put_weights(init_weights(0))
    adapter = StepAdapter(["a", "b"], hub)
    srv, client = start(adapter)
    try:
        tid = client.dispatch(1, {"ref": ref}, TC, ["a", "b"])
        part = client.collect_partial(tid)
        assert part["envelopes"] == [] and part["state"] in ("queued", "running")
        with pytest.raises(RpcError) as e:
            client.collect(tid)
        assert e.value.code == -32013
        adapter.go["a"].set()
        st = wait_for(lambda: (s := client.get_task(tid))["sites"]["a"] == "done" and s)
        assert st["state"] == "running" and st["sites"]["b"] == "running" and st["progress"]["done"] == 1
        part = client.collect_partial(tid)
        assert [e["site_id"] for e in part["envelopes"]] == ["a"] and part["sites"]["b"] == "running"
        env = Envelope.from_dict(part["envelopes"][0])
        assert envelope_problems(env, round=1, base_model=ref, expected_sites=["a", "b"], protocol="flhub/0.2") == []
        with pytest.raises(RpcError) as e:  # b is still running
            client.dispatch(2, {"ref": ref}, TC, ["a", "b"])
        assert e.value.code == -32015
        with pytest.raises(RpcError) as e:
            client.cancel(tid, sites=["zzz"])
        assert e.value.code == -32602
        res = client.cancel(tid, sites=["b"])
        assert res["state"] == "cancelled" and res["sites"] == {"a": "done", "b": "cancelled"}
        assert [e["site_id"] for e in client.collect_partial(tid)["envelopes"]] == ["a"]
        tid2 = client.dispatch(2, {"ref": ref}, TC, ["a", "b"])  # accepted right away, no -32015
        adapter.go["b"].set()
        wait_for(lambda: client.get_task(tid2)["state"] == "completed")
        assert [e["site_id"] for e in client.collect(tid2)] == ["a", "b"]
    finally:
        srv.stop()


def test_site_cancel_keeps_other_sites_running(hub):
    ref = hub.put_weights(init_weights(0))
    adapter = StepAdapter(["a", "b"], hub)
    srv, client = start(adapter)
    try:
        tid = client.dispatch(1, {"ref": ref}, TC, ["a", "b"])
        wait_for(lambda: client.get_task(tid)["state"] == "running")
        res = client.cancel(tid, sites=["a"])
        assert res["state"] == "running" and res["sites"]["a"] == "cancelled"
        assert adapter.cancel_calls == [["a"]]
        adapter.go["a"].set()  # late result of a cancelled site: dropped
        adapter.go["b"].set()
        st = wait_for(lambda: (s := client.get_task(tid))["state"] != "running" and s)
        assert st["state"] == "cancelled" and st["sites"] == {"a": "cancelled", "b": "done"}
        assert [e["site_id"] for e in client.collect_partial(tid)["envelopes"]] == ["b"]
    finally:
        srv.stop()


def test_deadline_keeps_done_sites_for_partial_collect(hub):
    ref = hub.put_weights(init_weights(0))
    adapter = StepAdapter(["a", "b"], hub)
    adapter.go["a"].set()
    srv, client = start(adapter)
    try:
        tid = client.dispatch(1, {"ref": ref}, TC, ["a", "b"], deadline_s=0.6)
        st = wait_for(lambda: (s := client.get_task(tid))["state"] == "failed" and s)
        assert st["sites"] == {"a": "done", "b": "failed"} and "deadline" in st["site_errors"]["b"]
        assert [e["site_id"] for e in client.collect_partial(tid)["envelopes"]] == ["a"]
    finally:
        srv.stop()


def test_v1_caller_gets_v1_envelopes(hub):
    ref = hub.put_weights(init_weights(0))
    adapter = StepAdapter(["a"], hub)
    adapter.go["a"].set()
    srv, _ = start(adapter)
    client = AdapterClient(srv.url, protocol="flhub/0.1")
    try:
        tid = client.dispatch(1, {"ref": ref}, TC, ["a"])
        wait_for(lambda: client.get_task(tid)["state"] == "completed")
        assert client.collect(tid)[0]["protocol"] == "flhub/0.1"
    finally:
        srv.stop()


class FakeRelayAdapter(RelayAdapter):
    adapter_name = framework = "fakerelay"


def test_relay_site_posts_and_site_cancel(hub):
    ref = hub.put_weights(init_weights(0))
    adapter = FakeRelayAdapter(["s1", "s2"], hub)
    srv, client = start(adapter)
    relay = RelayClient(srv.url, hub.base_url)
    try:
        tid = client.dispatch(3, {"ref": ref}, TC, ["s1", "s2"])
        job = wait_for(lambda: (j := relay.next_job(timeout=1))["type"] == "train" and j)
        model = relay.get_model(job)
        assert relay.post_site_result(job["job_id"], relay.result("s1", model, 4, {"train_loss": 1.0}, "v"))
        assert client.get_task(tid)["sites"] == {"s1": "done", "s2": "running"}
        client.cancel(tid, sites=["s2"])
        assert relay.job_status(job["job_id"])["sites"] == {"s1": "done", "s2": "cancelled"}
        assert not relay.post_site_result(job["job_id"], relay.result("s2", model, 4, {}, "v"))  # dropped
        relay.post_result(job["job_id"], [])  # a final post after the end is harmless
        st = client.get_task(tid)
        assert st["state"] == "cancelled" and st["sites"]["s2"] == "cancelled"
        tid2 = client.dispatch(4, {"ref": ref}, TC, ["s1", "s2"])
        job = wait_for(lambda: (j := relay.next_job(timeout=1))["type"] == "train" and j)
        relay.post_site_error(job["job_id"], "s2", "client crashed")
        relay.post_site_result(job["job_id"], relay.result("s1", model, 4, {}, "v"))
        st = wait_for(lambda: (s := client.get_task(tid2))["state"] != "running" and s)
        assert st["state"] == "failed" and st["site_errors"] == {"s2": "client crashed"}
        assert relay.job_status("nope") is None
    finally:
        srv.stop()


def test_relay_skips_sites_cancelled_while_queued(hub):
    ref = hub.put_weights(init_weights(0))
    adapter = FakeRelayAdapter(["s1", "s2"], hub)
    srv, client = start(adapter)
    relay = RelayClient(srv.url, hub.base_url)
    try:
        tid = client.dispatch(1, {"ref": ref}, TC, ["s1", "s2"])
        wait_for(lambda: client.get_task(tid)["state"] == "running")
        client.cancel(tid, sites=["s1"])
        job = wait_for(lambda: (j := relay.next_job(timeout=1))["type"] == "train" and j)
        assert job["sites"] == ["s2"]
    finally:
        srv.stop()


def test_hub_core_streaming_delete_and_gc(hub):
    data = bytes(range(256)) * 20000  # 5 MB, several chunks
    ref = hub.put_blob(data)
    assert hub.get_blob(ref) == data and hub.put_blob(data) == ref
    status, _ = http_request(hub.blob_url("sha256:" + "0" * 64), "PUT", data)
    assert status == 400
    assert hub.delete_blob(ref) and not hub.has_blob(ref) and not hub.delete_blob(ref)
    hub.put_blob(b"x")
    assert hub.gc(older_than_s=3600)["deleted"] == 0
    assert hub.gc(older_than_s=0)["deleted"] == 1


def test_data_variant_hint_reaches_task_and_relay(hub):
    ref = hub.put_weights(init_weights(0))
    adapter = FakeRelayAdapter(["s1"], hub)
    srv, client = start(adapter)
    relay = RelayClient(srv.url, hub.base_url)
    try:
        with pytest.raises(RpcError) as e:
            client.dispatch(1, {"ref": ref}, TC, ["s1"], data_variant="")
        assert e.value.code == -32602
        tid = client.dispatch(1, {"ref": ref}, TC, ["s1"], data_variant="organamnist-dir0.3")
        assert adapter.tasks.get(tid).data_variant == "organamnist-dir0.3"
        job = wait_for(lambda: (j := relay.next_job(timeout=1))["type"] == "train" and j)
        assert job["data_variant"] == "organamnist-dir0.3"
    finally:
        srv.stop()

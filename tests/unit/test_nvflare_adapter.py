"""Unit tests for the NVFlare adapter's relay-job keeper and site status (no NVFlare server needed)."""

import threading
import time

import pytest

pytest.importorskip("nvflare")

from flhub_core.model import init_weights  # noqa: E402

from adapters.common import HubClient, RelayClient  # noqa: E402
from adapters.nvflare import service  # noqa: E402
from adapters.nvflare.service import NVFlareAdapter  # noqa: E402
from hub.core.server import HubCore  # noqa: E402

SITES = ["nvf-h1", "nvf-h2"]
TC = {"local_epochs": 1, "batch_size": 64, "lr": 0.05, "momentum": 0.0, "seed": 2026}


class FakeAdmin:
    """Stands in for FlareAdmin: a job table plus the clients' answers to the job-status probe."""

    username = "admin@test"

    def __init__(self):
        self.jobs = []  # dicts like FLARE API list_jobs() rows
        self.clients = {}  # client -> set of job ids (missing client = no reply)
        self.submitted, self.aborted, self.deleted, self.ghosts = [], [], [], set()

    def active_jobs(self, name):
        return [j for j in reversed(self.jobs) if j["status"] in ("SUBMITTED", "RUNNING")]

    def submit_job(self, job_dir):
        job_id = f"job-{len(self.submitted) + 1}"  # unique, like NVFlare's job uuids
        self.jobs.append({"job_id": job_id, "job_name": "flhub-relay", "status": "RUNNING"})
        self.submitted.append(job_id)
        return job_id

    def abort_job(self, job_id):
        if job_id in self.ghosts:  # listed as RUNNING, unknown to the server's job runner
            raise RuntimeError(f"server internal error: Job {job_id} is not running.")
        for j in self.jobs:
            if j["job_id"] == job_id:
                j["status"] = "FINISHED:ABORTED"
        self.aborted.append(job_id)

    def delete_job(self, job_id):
        self.jobs = [j for j in self.jobs if j["job_id"] != job_id]
        self.deleted.append(job_id)

    def client_jobs(self):
        return {c: set(j) for c, j in self.clients.items()}


@pytest.fixture
def hub(tmp_path):
    srv = HubCore(tmp_path / "hub").build_server("127.0.0.1", 0).start()
    yield HubClient(srv.url)
    srv.stop()


def make_adapter(hub, sites=SITES, base_sites=None):
    admin = FakeAdmin()
    admin.clients = {s: set() for s in (base_sites or sites)}  # connected, in no job yet
    ad = NVFlareAdapter(sites, admin, "/tmp/flhub-relay", hub, base_sites=base_sites)
    ad.srv = ad.build_server("127.0.0.1", 0).start()
    ad.relay = RelayClient(ad.srv.url, hub.base_url)  # plays the relay inside the NVFlare server job
    return ad


@pytest.fixture
def adapter(hub):
    ad = make_adapter(hub)
    yield ad
    ad.srv.stop()


def go_online(ad, sites=SITES, job="job-1"):
    ad.relay.report_sites({s: "online" for s in sites})
    ad.admin.clients.update({s: {job} for s in sites})
    ad.client_jobs(max_age_s=0)


def test_keeper_submits_once_and_adopts(adapter):
    adapter.keep_once()
    adapter.keep_once()
    assert adapter.admin.submitted == ["job-1"] and adapter.relay_job_id == "job-1"


def test_keeper_waits_for_base_sites_then_grace(adapter, monkeypatch):
    adapter.admin.clients = {}
    adapter.keep_once()
    assert adapter.admin.submitted == []  # nobody connected
    adapter.admin.clients = {"nvf-h2": set()}
    adapter.keep_once()
    assert adapter.admin.submitted == []  # nvf-h1 may still come: one job start for both
    monkeypatch.setattr(service, "SUBMIT_GRACE_S", 0.0)
    adapter.keep_once()
    assert adapter.admin.submitted == ["job-1"]  # grace over: run with the site that is there


def test_offline_extra_sites_block_nothing(hub):
    ad = make_adapter(hub, sites=SITES + ["nvf-h7", "nvf-h8"], base_sites=SITES)
    try:
        ad.keep_once()  # base sites connected, extras absent: submit at once
        assert ad.admin.submitted == ["job-1"]
        go_online(ad)
        assert ad.site_status() == {"nvf-h1": "online", "nvf-h2": "online", "nvf-h7": "offline", "nvf-h8": "offline"}
    finally:
        ad.srv.stop()


def test_keeper_aborts_extra_jobs(adapter):
    adapter.admin.submit_job("x")
    adapter.admin.submit_job("x")
    adapter.keep_once()
    assert adapter.admin.aborted == ["job-1"] and adapter.relay_job_id == "job-2"


def test_site_online_needs_relay_view_and_client_probe(adapter):
    adapter.keep_once()
    assert adapter.site_status() == {"nvf-h1": "offline", "nvf-h2": "offline"}  # relay silent
    adapter.relay.report_sites({"nvf-h1": "online", "nvf-h2": "online"})
    adapter.admin.clients = {"nvf-h1": {"job-1"}, "nvf-h2": {"job-1"}}
    assert adapter.client_jobs(max_age_s=0) and adapter.site_status() == {"nvf-h1": "online", "nvf-h2": "online"}
    adapter.admin.clients = {"nvf-h2": {"job-1"}}  # nvf-h1 died: no reply to the probe
    adapter.client_jobs(max_age_s=0)
    assert adapter.site_status() == {"nvf-h1": "offline", "nvf-h2": "online"}


def test_offline_sites_fail_alone(adapter):
    adapter.keep_once()
    task = adapter.tasks.create("nvflare", 1, SITES, "sha256:" + "0" * 64, TC, 60)
    adapter.tasks.start(task)
    assert adapter.run_round(task, {}, {}) == []  # nobody online: every site fails at once
    assert task.state == "failed" and set(task.status()["site_errors"]) == set(SITES)


def run_round_async(ad, task, hub):
    weights = init_weights(2026)
    ref = hub.put_weights(weights)
    task.model_ref = ref
    threading.Thread(target=ad._work, args=(task,), daemon=True).start()
    return weights


def test_round_runs_on_the_online_site(adapter, hub):
    adapter.keep_once()
    go_online(adapter, ["nvf-h2"])
    task = adapter.tasks.create("nvflare", 3, SITES, "sha256:" + "0" * 64, TC, 60)
    weights = run_round_async(adapter, task, hub)
    job = adapter.relay.next_job(timeout=5)
    assert job["type"] == "train" and job["sites"] == ["nvf-h2"]  # nvf-h1 failed at dispatch
    assert adapter.relay.post_site_result(job["job_id"], adapter.relay.result("nvf-h2", weights, 7, {}, "v"))
    end = time.time() + 5
    while not task.terminal and time.time() < end:
        time.sleep(0.02)
    assert task.state == "failed" and task.site_state == {"nvf-h1": "failed", "nvf-h2": "done"}
    assert [e["site_id"] for e in task.envelopes] == ["nvf-h2"]


def test_site_that_goes_offline_mid_round_fails(adapter, hub, monkeypatch):
    monkeypatch.setattr(service, "OFFLINE_FAIL_S", 0.0)
    monkeypatch.setattr(service, "SITE_WATCH_S", 0.05)
    monkeypatch.setattr(service, "CLIENT_JOBS_TTL_S", 0.0)
    adapter.keep_once()
    go_online(adapter)
    task = adapter.tasks.create("nvflare", 4, SITES, "sha256:" + "0" * 64, TC, 60)
    run_round_async(adapter, task, hub)
    job = adapter.relay.next_job(timeout=5)
    assert job["sites"] == SITES
    adapter.admin.clients = {"nvf-h2": {"job-1"}}  # nvf-h1 killed mid-round
    end = time.time() + 5
    while task.site_state["nvf-h1"] != "failed" and time.time() < end:
        time.sleep(0.02)
    assert task.site_state == {"nvf-h1": "failed", "nvf-h2": "running"}
    assert adapter.relay.job_status(job["job_id"])["sites"]["nvf-h1"] == "failed"  # the relay cancels it


def test_reconnected_site_outside_job_triggers_graceful_restart(adapter, monkeypatch):
    monkeypatch.setattr(service, "REJOIN_AFTER_S", 0.0)
    adapter.keep_once()
    adapter.admin.clients = {"nvf-h1": set(), "nvf-h2": {"job-1"}}  # nvf-h1 back, but not in the job
    adapter.keep_once()
    assert adapter._shutdown.is_set(), "relay should be asked to end its job"
    assert adapter.relay.next_job(timeout=1)["type"] == "shutdown"
    adapter.admin.jobs[0]["status"] = "FINISHED:COMPLETED"  # the relay ended the job
    adapter.keep_once()
    assert adapter.admin.submitted == ["job-1", "job-2"] and adapter.relay_job_id == "job-2"


def test_no_restart_while_a_round_is_in_flight(adapter, monkeypatch):
    monkeypatch.setattr(service, "REJOIN_AFTER_S", 0.0)
    adapter.keep_once()
    adapter.admin.clients = {"nvf-h1": set(), "nvf-h2": {"job-1"}}
    adapter._queue.put({"type": "train", "job_id": "t"})  # a dispatched round waits for the relay
    adapter.keep_once()
    assert not adapter._shutdown.is_set()


def test_job_that_ignores_shutdown_is_aborted(adapter, monkeypatch):
    monkeypatch.setattr(service, "REJOIN_AFTER_S", 0.0)
    monkeypatch.setattr(service, "SHUTDOWN_GRACE_S", 0.0)
    adapter.keep_once()
    adapter.admin.clients = {"nvf-h1": set(), "nvf-h2": {"job-1"}}
    adapter.keep_once()  # asks for shutdown
    adapter.keep_once()  # grace over, job still running -> abort
    adapter.keep_once()  # nothing active -> new job
    assert adapter.admin.aborted == ["job-1"] and adapter.admin.submitted == ["job-1", "job-2"]


def test_stale_job_after_server_restart_is_replaced(adapter, monkeypatch):
    """After a server restart the job store can list the old relay job as RUNNING although the
    server does not run it (abort: 'not running'): the keeper deletes it and submits a new one."""
    monkeypatch.setattr(service, "REJOIN_AFTER_S", 0.0)
    adapter.keep_once()
    adapter.admin.ghosts.add("job-1")
    adapter.admin.clients = {"nvf-h1": set(), "nvf-h2": set()}  # reconnected, in no job
    adapter.keep_once()  # outside the job -> shutdown asked; the relay is silent (never polled)
    adapter.keep_once()  # relay silent -> end the job at once: abort fails -> delete
    assert adapter.admin.deleted == ["job-1"]
    adapter.keep_once()  # nothing active -> new job
    assert adapter.admin.submitted == ["job-1", "job-2"] and adapter.relay_job_id == "job-2"

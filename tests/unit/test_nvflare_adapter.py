"""Unit tests for the NVFlare adapter's relay-job keeper and site status (no NVFlare server needed)."""

import pytest

pytest.importorskip("nvflare")

from adapters.common import FrameworkError, RelayClient  # noqa: E402
from adapters.nvflare import service  # noqa: E402
from adapters.nvflare.service import NVFlareAdapter  # noqa: E402

SITES = ["nvf-h1", "nvf-h2"]
TC = {"local_epochs": 1, "batch_size": 64, "lr": 0.05, "momentum": 0.0, "seed": 2026}


class FakeAdmin:
    """Stands in for FlareAdmin: a job table plus the clients' answers to the job-status probe."""

    username = "admin@test"

    def __init__(self):
        self.jobs = []  # dicts like FLARE API list_jobs() rows
        self.clients = {}  # client -> set of job ids (missing client = no reply)
        self.submitted, self.aborted = [], []

    def active_jobs(self, name):
        return [j for j in reversed(self.jobs) if j["status"] in ("SUBMITTED", "RUNNING")]

    def submit_job(self, job_dir):
        job_id = f"job-{len(self.jobs) + 1}"
        self.jobs.append({"job_id": job_id, "job_name": "flhub-relay", "status": "RUNNING"})
        self.submitted.append(job_id)
        return job_id

    def abort_job(self, job_id):
        for j in self.jobs:
            if j["job_id"] == job_id:
                j["status"] = "FINISHED:ABORTED"
        self.aborted.append(job_id)

    def client_jobs(self):
        return {c: set(j) for c, j in self.clients.items()}


@pytest.fixture
def adapter():
    admin = FakeAdmin()
    ad = NVFlareAdapter(SITES, admin, "/tmp/flhub-relay")
    srv = ad.build_server("127.0.0.1", 0).start()
    ad.relay = RelayClient(srv.url)  # plays the relay inside the NVFlare server job
    yield ad
    srv.stop()


def test_keeper_submits_once_and_adopts(adapter):
    adapter.keep_once()
    adapter.keep_once()
    assert adapter.admin.submitted == ["job-1"] and adapter.relay_job_id == "job-1"


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


def test_run_round_fails_fast_when_a_site_is_offline(adapter):
    adapter.keep_once()
    task = adapter.tasks.create("nvflare", 1, SITES, "sha256:" + "0" * 64, TC, 60)
    with pytest.raises(FrameworkError, match="not online"):
        adapter.run_round(task, {}, {})


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

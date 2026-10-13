"""NVFlare adapter service: flhub/0.1 JSON-RPC + relay routes on 127.0.0.1:8811.

Built on adapters.common.RelayAdapter. With the operator's admin startup kit (FLARE API) it
keeps exactly one long-running NVFlare relay job ("flhub-relay", see adapters/nvflare/job.py)
alive, and restarts it gracefully when a hospital client (re)connects that is not part of it.
A site is online only when the relay reports it as a live job client AND the client answers
the server's job-status probe for the running relay job.

    python -m adapters.nvflare.service --admin-kit DIR --job-dir DIR [--port 8811] [--sites nvf-h1 nvf-h2]
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import threading
import time

import nvflare

from adapters.common.base import FrameworkError
from adapters.common.hubclient import HubClient
from adapters.common.relay import RelayAdapter
from adapters.nvflare.flare_admin import FlareAdmin
from adapters.nvflare.job import JOB_NAME

log = logging.getLogger("flhub.adapter.nvflare")

KEEPER_PERIOD_S = 10.0
CLIENT_JOBS_TTL_S = 5.0
REJOIN_AFTER_S = 15.0  # a connected site outside the relay job for this long triggers a job restart
SHUTDOWN_GRACE_S = 60.0  # then abort the old job if it did not end by itself


class NVFlareAdapter(RelayAdapter):
    adapter_name = "nvflare"
    framework = "nvflare"
    framework_version = nvflare.__version__

    def __init__(self, sites: list[str], admin: FlareAdmin, job_dir: str, hub: HubClient | None = None) -> None:
        super().__init__(sites, hub)
        self.admin = admin
        self.job_dir = os.path.abspath(os.path.expanduser(job_dir))
        self.relay_job_id: str | None = None
        self._client_jobs: tuple[float, dict[str, set[str]]] = (float("-inf"), {})
        self._outside_since: dict[str, float] = {}
        self._restart_asked: float | None = None

    # ---- site status ---------------------------------------------------------------------
    def client_jobs(self, max_age_s: float = CLIENT_JOBS_TTL_S) -> dict[str, set[str]]:
        """{client: job ids} from the server's probe of the clients, cached for a few seconds."""
        stamp, jobs = self._client_jobs
        if time.monotonic() - stamp > max_age_s:
            try:
                jobs = self.admin.client_jobs()
            except Exception as exc:
                log.warning("FLARE API client job status failed: %s", exc)
                jobs = {}
            self._client_jobs = (time.monotonic(), jobs)
        return jobs

    def site_status(self) -> dict[str, str]:
        relay_view = super().site_status()  # the relay's live job clients ({} if the relay is silent)
        jobs = self.client_jobs()
        return {
            s: "online" if relay_view.get(s) == "online" and self.relay_job_id in jobs.get(s, ()) else "offline"
            for s in self.sites
        }

    def run_round(self, task, weights, client_config):
        offline = [s for s, st in self.site_status().items() if s in task.sites and st != "online"]
        if offline:  # fail fast instead of waiting for the deadline
            raise FrameworkError(f"NVFlare sites not online in the relay job: {offline}")
        return super().run_round(task, weights, client_config)

    # ---- relay job lifecycle -------------------------------------------------------------
    def _busy(self) -> bool:
        return self._queue.qsize() > 0 or bool(self._pending)

    def keep_relay_job(self, stop: threading.Event) -> None:
        while not stop.is_set():
            try:
                self.keep_once()
            except Exception as exc:
                log.warning("relay job check failed: %s", exc)
            stop.wait(KEEPER_PERIOD_S)

    def keep_once(self) -> None:
        """Submit the relay job if none is active; restart it when a connected site is outside it."""
        active = self.admin.active_jobs(JOB_NAME)
        for old in active[1:]:
            log.warning("aborting extra relay job %s", old["job_id"])
            self.admin.abort_job(old["job_id"])
        if not active:
            self._restart_asked = None
            self._outside_since.clear()
            self._shutdown.clear()  # a shutdown nobody picked up must not end the new relay
            self.relay_job_id = self.admin.submit_job(self.job_dir)
            log.info("submitted relay job %s from %s", self.relay_job_id, self.job_dir)
            return
        job = active[0]
        if job["job_id"] != self.relay_job_id:
            self.relay_job_id = job["job_id"]
            log.info("relay job %s is %s", self.relay_job_id, job["status"])
        if self._restart_asked is not None:
            if time.monotonic() - self._restart_asked > SHUTDOWN_GRACE_S:
                log.warning("relay job %s did not end after shutdown; aborting it", self.relay_job_id)
                self.admin.abort_job(self.relay_job_id)
                self._restart_asked = None
            return
        if job["status"] != "RUNNING":
            return  # scheduled; waits for its clients
        jobs = self.client_jobs(max_age_s=0)
        now = time.monotonic()
        for site in self.sites:
            if site in jobs and self.relay_job_id not in jobs[site]:
                self._outside_since.setdefault(site, now)
            else:
                self._outside_since.pop(site, None)
        late = [s for s, t in self._outside_since.items() if now - t >= REJOIN_AFTER_S]
        if late and not self._busy():
            log.info("sites %s are connected but outside relay job %s; restarting the job", late, self.relay_job_id)
            self.request_relay_shutdown()
            self._restart_asked = now
            self._outside_since.clear()

    def shutdown_relay(self, wait_s: float = 30.0) -> None:
        """Asks the relay to end its job; aborts the job through the FLARE API if it does not."""
        try:
            seen = self.relay_seen_s()
            if seen is not None and seen < 60:
                self.request_relay_shutdown()
                end = time.monotonic() + wait_s
                while self._shutdown.is_set() and time.monotonic() < end:
                    time.sleep(0.5)
                end = time.monotonic() + 20.0  # the job needs a few seconds to finish after run() returned
                while self.admin.active_jobs(JOB_NAME) and time.monotonic() < end:
                    time.sleep(1.0)
            for job in self.admin.active_jobs(JOB_NAME):
                log.info("aborting relay job %s (%s)", job["job_id"], job["status"])
                self.admin.abort_job(job["job_id"])
        except Exception as exc:
            log.warning("could not check/abort relay jobs: %s", exc)

    def _route_status(self, req):
        status, body = super()._route_status(req)
        return status, {**body, "relay_job_id": self.relay_job_id,
                        "client_jobs": {k: sorted(v) for k, v in self._client_jobs[1].items()}}


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m adapters.nvflare.service")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8811)
    ap.add_argument("--sites", nargs="+", default=["nvf-h1", "nvf-h2"], help="site ids (space or comma separated)")
    ap.add_argument("--hub-url", default=os.environ.get("FLHUB_HUB_URL"))
    ap.add_argument("--admin-kit", required=True, help="NVFlare admin startup kit directory")
    ap.add_argument("--job-dir", required=True, help="exported relay job folder (python -m adapters.nvflare.job)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    sites = [s for item in args.sites for s in item.split(",") if s]

    admin = FlareAdmin(args.admin_kit)
    adapter = NVFlareAdapter(sites, admin, args.job_dir, HubClient(args.hub_url))
    server = adapter.build_server(args.host, args.port).start()
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    threading.Thread(target=adapter.keep_relay_job, args=(stop,), name="relay-keeper", daemon=True).start()
    log.info("NVFlare adapter %s for sites %s (admin %s)", server.url, sites, admin.username)
    stop.wait()
    log.info("stopping: ending the relay job")
    adapter.shutdown_relay()
    server.stop()
    admin.close()


if __name__ == "__main__":
    main()

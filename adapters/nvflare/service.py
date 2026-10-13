"""NVFlare adapter service: flhub/0.1 JSON-RPC + relay routes on 127.0.0.1:8811.

Built on adapters.common.RelayAdapter. With the operator's admin startup kit (FLARE API) it
keeps exactly one long-running NVFlare relay job ("flhub-relay", see adapters/nvflare/job.py)
alive, and restarts it gracefully when a hospital client (re)connects that is not part of it.
A site is online only when the relay reports it as a live job client AND the client answers
the server's job-status probe for the running relay job.

flhub/0.2: a round runs on the online sites of its dispatch; an offline site, or one that goes
offline during the round, fails on its own (the others go on). Registered sites that are not
connected (e.g. the E5 extra sites) never block anything; --base-sites only shapes when the
relay job is (re)submitted.

    python -m adapters.nvflare.service --admin-kit DIR --job-dir DIR [--port 8811] [--sites nvf-h1 nvf-h2]
        [--base-sites nvf-h1 nvf-h2]
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import threading
import time

import nvflare

from adapters.common.hubclient import HubClient
from adapters.common.relay import RelayAdapter
from adapters.nvflare.flare_admin import FlareAdmin
from adapters.nvflare.job import JOB_NAME

log = logging.getLogger("flhub.adapter.nvflare")

KEEPER_PERIOD_S = 10.0
CLIENT_JOBS_TTL_S = 5.0
REJOIN_AFTER_S = 15.0  # a connected site outside the relay job for this long triggers a job restart
SHUTDOWN_GRACE_S = 60.0  # then abort the old job if it did not end by itself
SUBMIT_GRACE_S = 30.0  # submit once every base site is connected, or this long after the first one
RELAY_DEAD_S = 30.0  # no contact from the relay for this long: its job is dead, end it without a shutdown wait
OFFLINE_FAIL_S = 30.0  # a site of a running round that is offline this long fails (the others go on)
SITE_WATCH_S = 5.0


class NVFlareAdapter(RelayAdapter):
    adapter_name = "nvflare"
    framework = "nvflare"
    framework_version = nvflare.__version__

    def __init__(self, sites: list[str], admin: FlareAdmin, job_dir: str, hub: HubClient | None = None,
                 base_sites: list[str] | None = None) -> None:
        super().__init__(sites, hub)
        self.admin = admin
        self.job_dir = os.path.abspath(os.path.expanduser(job_dir))
        self.base_sites = [s for s in (base_sites or sites) if s in sites]
        self.relay_job_id: str | None = None
        self._client_jobs: tuple[float, dict[str, set[str]] | None] = (float("-inf"), None)
        self._outside_since: dict[str, float] = {}
        self._restart_asked: float | None = None
        self._first_connected: float | None = None
        self._relay_contact = 0.0  # last relay request of any kind (next, job status, site report)
        self._stale: set[str] = set()  # jobs the job store lists as active but the server does not run

    # ---- site status ---------------------------------------------------------------------
    def client_jobs(self, max_age_s: float | None = None) -> dict[str, set[str]] | None:
        """{client: job ids} from the server's probe of the clients, cached for CLIENT_JOBS_TTL_S;
        None if the probe failed (unknown)."""
        stamp, jobs = self._client_jobs
        if time.monotonic() - stamp > (CLIENT_JOBS_TTL_S if max_age_s is None else max_age_s):
            try:
                jobs = self.admin.client_jobs()
            except Exception as exc:
                log.warning("FLARE API client job status failed: %s", exc)
                jobs = None
            self._client_jobs = (time.monotonic(), jobs)
        return jobs

    def site_status(self) -> dict[str, str]:
        relay_view = super().site_status()  # the relay's live job clients ({} if the relay is silent)
        jobs = self.client_jobs() or {}
        return {
            s: "online" if relay_view.get(s) == "online" and self.relay_job_id in jobs.get(s, ()) else "offline"
            for s in self.sites
        }

    def run_round(self, task, weights, client_config):
        status = self.site_status()
        for s in task.active_sites():  # fail offline sites at once; the others go on
            if status.get(s) != "online":
                self.report_site_error(task, s, f"NVFlare site {s} is not online in relay job {self.relay_job_id}")
        if not task.active_sites():
            return []
        stop = threading.Event()
        threading.Thread(target=self._watch_sites, args=(task, stop), name=f"{task.task_id}-sites",
                         daemon=True).start()
        try:
            return super().run_round(task, weights, client_config)
        finally:
            stop.set()

    def _watch_sites(self, task, stop: threading.Event) -> None:
        """Fails a site of the running round once it has been offline for OFFLINE_FAIL_S (e.g. a
        killed client), instead of letting the round wait for the deadline. An unknown status
        (FLARE API probe failed) never fails a site."""
        offline_since: dict[str, float] = {}
        while not stop.wait(SITE_WATCH_S) and not task.terminal:
            if self.client_jobs() is None:
                continue
            status, now = self.site_status(), time.monotonic()
            for s in task.active_sites():
                if status.get(s) == "online":
                    offline_since.pop(s, None)
                elif now - offline_since.setdefault(s, now) >= OFFLINE_FAIL_S:
                    self.report_site_error(task, s, f"NVFlare site {s} went offline during round {task.round} "
                                                    f"(not in relay job {self.relay_job_id} for {OFFLINE_FAIL_S:.0f}s)")

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

    def relay_contact_s(self) -> float | None:
        """Seconds since the relay last contacted the adapter (polls, job status, site reports)."""
        seen = [t for t in (self._relay_contact, self._last_poll) if t]
        return time.monotonic() - max(seen) if seen else None

    def end_job(self, job_id: str) -> None:
        """Aborts a relay job. A job the job store still lists as running but the server does not run
        (left over from a server restart: abort fails with 'not running') is deleted or ignored."""
        try:
            self.admin.abort_job(job_id)
        except Exception as exc:
            if "not running" not in str(exc):
                raise
            log.warning("relay job %s is stale (%s); deleting it from the job store", job_id, exc)
            self._stale.add(job_id)
            try:
                self.admin.delete_job(job_id)
            except Exception as exc2:
                log.warning("could not delete stale job %s (%s); ignoring it", job_id, exc2)

    def keep_once(self) -> None:
        """Submit the relay job if none is active; restart it when a connected site is outside it."""
        active = [j for j in self.admin.active_jobs(JOB_NAME) if j["job_id"] not in self._stale]
        for old in active[1:]:
            log.warning("aborting extra relay job %s", old["job_id"])
            self.end_job(old["job_id"])
        if not active:
            self._restart_asked = None
            self._outside_since.clear()
            jobs = self.client_jobs(max_age_s=0)
            connected = [s for s in self.sites if s in (jobs or {})]
            if not connected:
                self._first_connected = None
                return  # nothing to run on yet
            now = time.monotonic()
            self._first_connected = self._first_connected or now
            missing = [s for s in self.base_sites if s not in connected]
            if missing and now - self._first_connected < SUBMIT_GRACE_S:
                return  # give the other base sites a moment, so that one job start covers all of them
            self._first_connected = None
            self._shutdown.clear()  # a shutdown nobody picked up must not end the new relay
            self.relay_job_id = self.admin.submit_job(self.job_dir)
            log.info("submitted relay job %s from %s (connected: %s)", self.relay_job_id, self.job_dir, connected)
            return
        job = active[0]
        if job["job_id"] != self.relay_job_id:
            self.relay_job_id = job["job_id"]
            log.info("relay job %s is %s", self.relay_job_id, job["status"])
        if self._restart_asked is not None:
            contact = self.relay_contact_s()
            dead = contact is None or contact > RELAY_DEAD_S
            if dead or time.monotonic() - self._restart_asked > SHUTDOWN_GRACE_S:
                log.warning("relay job %s did not end after shutdown (relay %s); aborting it", self.relay_job_id,
                            "silent" if dead else "alive")
                self._restart_asked = None
                self.end_job(self.relay_job_id)
            return
        if job["status"] != "RUNNING":
            return  # scheduled; waits for its clients
        jobs = self.client_jobs(max_age_s=0)
        if jobs is None:
            return  # probe failed: decide nothing
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

    def _route_job(self, req):
        self._relay_contact = time.monotonic()
        return super()._route_job(req)

    def _route_sites(self, req):
        self._relay_contact = time.monotonic()
        return super()._route_sites(req)

    def _route_status(self, req):
        status, body = super()._route_status(req)
        return status, {**body, "relay_job_id": self.relay_job_id,
                        "client_jobs": {k: sorted(v) for k, v in (self._client_jobs[1] or {}).items()}}


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m adapters.nvflare.service")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8811)
    ap.add_argument("--sites", nargs="+", default=["nvf-h1", "nvf-h2"], help="site ids (space or comma separated)")
    ap.add_argument("--base-sites", nargs="+", default=None,
                    help="sites that normally run (default: all --sites); extra sites may stay offline")
    ap.add_argument("--hub-url", default=os.environ.get("FLHUB_HUB_URL"))
    ap.add_argument("--admin-kit", required=True, help="NVFlare admin startup kit directory")
    ap.add_argument("--job-dir", required=True, help="exported relay job folder (python -m adapters.nvflare.job)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    sites = [s for item in args.sites for s in item.split(",") if s]
    base = [s for item in (args.base_sites or []) for s in item.split(",") if s] or None

    admin = FlareAdmin(args.admin_kit)
    adapter = NVFlareAdapter(sites, admin, args.job_dir, HubClient(args.hub_url), base_sites=base)
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

"""Thread-safe wrapper around the FLARE API, used by the NVFlare adapter service.

The adapter logs in with the operator's admin startup kit (never committed). One session is
reused for all calls and recreated when NVFlare reports it expired or closed.
"""

import json
import logging
import os
import threading

from nvflare.fuel.flare_api.flare_api import new_secure_session

log = logging.getLogger("flhub.nvflare.admin")

ACTIVE_JOB_STATES = ("SUBMITTED", "APPROVED", "DISPATCHED", "RUNNING")


def admin_username(admin_kit: str) -> str:
    """Reads the admin user name from the kit's startup/fed_admin.json."""
    with open(os.path.join(admin_kit, "startup", "fed_admin.json")) as f:
        return json.load(f)["admin"]["username"]


class FlareAdmin:
    def __init__(self, admin_kit: str, username: str = None, timeout: float = 30.0):
        self.admin_kit = os.path.abspath(os.path.expanduser(admin_kit))
        self.username = username or admin_username(self.admin_kit)
        self.timeout = timeout
        self._lock = threading.Lock()
        self._session = None

    def _drop_session(self):
        if self._session is not None:
            try:
                self._session.close()
            except Exception:
                pass
            self._session = None

    def _call(self, fn):
        """Runs fn(session) under the lock; reconnects once if the session is unusable."""
        with self._lock:
            for attempt in (1, 2):
                try:
                    if self._session is None:
                        self._session = new_secure_session(self.username, self.admin_kit, timeout=self.timeout)
                    return fn(self._session)
                except Exception as e:
                    self._drop_session()
                    if attempt == 2:
                        raise
                    log.warning("FLARE API call failed (%s: %s); reconnecting", type(e).__name__, e)

    def close(self):
        with self._lock:
            self._drop_session()

    def connected_clients(self) -> list:
        """Names of the NVFlare clients currently connected to the server."""
        return sorted(c.name for c in self._call(lambda s: s.get_system_info()).client_info)

    def active_jobs(self, name: str) -> list:
        """Jobs with this name that are queued or running, newest first."""
        jobs = self._call(lambda s: s.list_jobs())
        jobs = [j for j in jobs if j.get("job_name") == name and j.get("status") in ACTIVE_JOB_STATES]
        return sorted(jobs, key=lambda j: j.get("submit_time", ""), reverse=True)

    def job_status(self, job_id: str) -> str:
        return self._call(lambda s: s.get_job_meta(job_id)).get("status", "UNKNOWN")

    def submit_job(self, job_dir: str) -> str:
        return self._call(lambda s: s.submit_job(job_dir))

    def abort_job(self, job_id: str):
        return self._call(lambda s: s.abort_job(job_id))

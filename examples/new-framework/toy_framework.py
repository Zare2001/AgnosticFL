"""A toy stand-in for a federated-learning framework, so that the skeletons run anywhere.

It plays the two parts a real framework has:
- the server API that an adapter (or a relay) calls: which sites are connected, send one
  round's work to a site, poll it, cancel it;
- the hospitals: they run the hospital code (client.py) on their own data. Here they run in
  one worker thread, one after another; real hospitals run in parallel on their own machines.

Replace it by your framework's API. Nothing in this file is part of the flhub library.
"""

from __future__ import annotations

import itertools
import threading
from concurrent.futures import Future, ThreadPoolExecutor

import client  # the hospital code next to this file


class ToyFramework:
    def __init__(self, sites: list[str], offline=(), data_dir: str | None = None) -> None:
        self.connected = {s: s not in set(offline) for s in sites}
        self._data = {s: client.load_data(s, data_dir) for s, up in self.connected.items() if up}
        self._hospitals = ThreadPoolExecutor(max_workers=1, thread_name_prefix="toy-hospitals")
        self._jobs: dict[str, Future] = {}
        self._ids = itertools.count(1)
        self._lock = threading.Lock()

    def connected_sites(self) -> dict[str, bool]:
        """{site: connected?}, as a framework server knows it."""
        return dict(self.connected)

    def submit(self, site: str, weights: dict, config: dict) -> str:
        """Send one round's work to one site; returns the framework's id for that work."""
        with self._lock:
            job_id = f"toy-{next(self._ids)}"
            if self.connected.get(site):
                self._jobs[job_id] = self._hospitals.submit(client.train_round, weights, config, self._data[site])
            else:
                self._jobs[job_id] = Future()
                self._jobs[job_id].set_exception(ConnectionError(f"site {site} is not connected"))
            return job_id

    def poll(self, job_id: str) -> tuple[str, object]:
        """("running", None), ("done", the site's reply) or ("failed", error text)."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.cancelled():
                self._jobs.pop(job_id, None)
                return "failed", "cancelled"
            if not job.done():
                return "running", None
            del self._jobs[job_id]
        if job.exception() is not None:
            return "failed", f"{type(job.exception()).__name__}: {job.exception()}"
        return "done", job.result()

    def cancel(self, job_id: str) -> None:
        """Best effort, as in most frameworks: work that already runs finishes and is ignored."""
        with self._lock:
            job = self._jobs.pop(job_id, None)
        if job is not None:
            job.cancel()

"""Skeleton adapter, direct pattern: the adapter calls the framework's server API.

    python examples/new-framework/adapter.py --port 8820 --hub http://127.0.0.1:8800 \
        --sites nvf-h1 nvf-h2 --offline toy-h7

Use this pattern when an outside process can send work to the sites through the framework's
API and fetch the replies (the vantage6 adapter, adapters/vantage6/, works this way). When the
round must run inside the framework's server process, use the relay pattern (relay.py), as the
NVFlare and Flower adapters do. Lines marked REPLACE are the framework calls; the toy framework
(toy_framework.py) stands in for them, so this file runs and passes the conformance suite.
"""

from __future__ import annotations

import argparse
import logging
import time

from adapters.common.base import BaseAdapter, SiteResult
from adapters.common.hubclient import HubClient

from toy_framework import ToyFramework  # REPLACE: your framework's server API


class ToyAdapter(BaseAdapter):
    """The base class implements the protocol (discover, dispatch, tasks/get, collect, cancel),
    the deadlines, the hub-core blobs and the envelopes. A framework adapter adds two hooks."""

    adapter_name = "toy"  # REPLACE: shown in discover and in task ids
    framework = "toy"  # REPLACE: the framework's name (in discover and in every envelope) ...
    framework_version = "0.1"  # ... and its version (in discover)

    def __init__(self, sites: list[str], hub: HubClient, offline=(), data_dir: str | None = None) -> None:
        super().__init__(sites, hub)
        self.fw = ToyFramework(sites, offline, data_dir)  # REPLACE: connect with your credentials
        self.jobs: dict[str, dict[str, str]] = {}  # task id -> {site: framework job id}, for cancel

    # Hook 1: which sites are connected now (answers discover).
    def site_status(self) -> dict[str, str]:
        return {s: "online" if up else "offline" for s, up in self.fw.connected_sites().items()}  # REPLACE

    # Hook 2: one round. It runs in a worker thread. The base class has fetched `weights` from the
    # hub core and stores the returned weights there; this hook only moves them to the sites and back.
    def run_round(self, task, weights, client_config) -> list[SiteResult]:
        # Send the weights and the config, unchanged, to every site of the task.
        jobs = {site: self.fw.submit(site, weights, client_config) for site in task.sites}  # REPLACE
        self.jobs[task.task_id] = jobs
        waiting = set(jobs)
        try:
            while waiting:
                if task.terminal:  # cancelled, past its deadline, or every site settled
                    return []
                for site in sorted(waiting):
                    if task.site_cancelled(site):  # per-site cancel: stop waiting for this site
                        waiting.discard(site)
                        continue
                    state, reply = self.fw.poll(jobs[site])  # REPLACE
                    if state == "running":
                        continue
                    waiting.discard(site)
                    if state == "failed":  # one site failed; the others go on
                        self.report_site_error(task, site, str(reply))
                    else:  # report each site at once: per-site progress and partial collect need it
                        self.report_site(task, SiteResult(site, reply["num_examples"], reply["metrics"],
                                                          reply["code_version"], weights=reply["weights"]))
                time.sleep(0.1)
        finally:
            self.jobs.pop(task.task_id, None)
        return []  # every site is reported already

    # Optional hooks: stop the framework's work (best effort; results that still come are dropped).
    def on_cancel(self, task) -> None:
        for job in self.jobs.get(task.task_id, {}).values():
            self.fw.cancel(job)  # REPLACE

    def on_cancel_sites(self, task, sites: list[str]) -> None:
        jobs = self.jobs.get(task.task_id, {})
        for site in sites:
            if site in jobs:
                self.fw.cancel(jobs[site])  # REPLACE


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8820)
    ap.add_argument("--hub", default="http://127.0.0.1:8800", help="hub core URL (blob store for the weights)")
    ap.add_argument("--sites", nargs="+", default=["nvf-h1", "nvf-h2"], help="sites this adapter serves")
    ap.add_argument("--offline", nargs="*", default=[], help="more registered sites that are not connected")
    ap.add_argument("--data-dir", default=None, help="toy hospitals' data (default $FLHUB_DATA_DIR)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    adapter = ToyAdapter(args.sites + args.offline, HubClient(args.hub), args.offline, args.data_dir)
    adapter.serve(args.host, args.port)  # the orchestrator talks to http://<host>:<port>


if __name__ == "__main__":
    main()

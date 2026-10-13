"""Mock adapter: flhub/0.1 with no framework at all; trains in-process with flhub_core.

    python -m adapters.mock [--port 8819] [--sites nvf-h1 nvf-h2 ...] [--data-dir ~/flhub-data]

The smallest complete BaseAdapter, and the orchestrator's test double.
"""

from __future__ import annotations

import argparse
import logging
import os
import time

import flhub_core
from flhub_core.data import SITES, load_site, materialize, site_path
from flhub_core.train import train

from adapters.common.base import BaseAdapter, FrameworkError, SiteResult
from adapters.common.hubclient import HubClient


class MockAdapter(BaseAdapter):
    adapter_name = "mock"
    framework = "mock"
    framework_version = flhub_core.__version__

    def __init__(self, sites, data_dir=None, hub: HubClient | None = None, delay_s: float = 0.0) -> None:
        super().__init__(sites, hub)
        self.paths = {s: site_path(s, data_dir) if site_path(s, data_dir).exists() else materialize(s, data_dir)
                      for s in sites}
        self.delay_s = delay_s
        self._data = {}

    def site_status(self) -> dict[str, str]:
        return {s: "online" if p.exists() else "offline" for s, p in self.paths.items()}

    def run_round(self, task, weights, client_config):
        results = []
        for site in task.sites:
            if task.cancelled:
                raise FrameworkError("cancelled")
            if site not in self._data:
                self._data[site] = load_site(self.paths[site])
            time.sleep(self.delay_s)
            new, n, metrics = train(weights, self._data[site], client_config)
            results.append(SiteResult(site, n, metrics, flhub_core.code_version(), weights=new))
            task.done += 1
        return results


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m adapters.mock")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8819)
    ap.add_argument("--sites", nargs="+", default=sorted(SITES, key=SITES.get))
    ap.add_argument("--data-dir", default=None)
    ap.add_argument("--hub", default=os.environ.get("FLHUB_HUB_URL"))
    ap.add_argument("--delay", type=float, default=0.0, help="extra seconds per site (to test polling)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    MockAdapter(args.sites, args.data_dir, HubClient(args.hub), args.delay).serve(args.host, args.port)

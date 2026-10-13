"""Mock adapter: flhub/0.2 with no framework at all; trains in-process with flhub_core.

    python -m adapters.mock [--port 8819] [--sites nvf-h1 nvf-h2 ...] [--data-dir ~/flhub-data]
                            [--delay S] [--site-delay SITE=S ...] [--hang SITE ...] [--fail SITE ...]
                            [--offline SITE ...]

The smallest complete BaseAdapter, and the orchestrator's test double. Sites train one after
another and each is reported as soon as it is done (per-site progress, partial collect).
--hang keeps a site running until it is cancelled or the deadline passes, --fail makes it fail,
--offline lists it as registered but offline (it then fails at once, like a dead hospital).
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

log = logging.getLogger("flhub.adapter.mock")


class MockAdapter(BaseAdapter):
    adapter_name = "mock"
    framework = "mock"
    framework_version = flhub_core.__version__

    def __init__(self, sites, data_dir=None, hub: HubClient | None = None, delay_s: float = 0.0, *,
                 site_delay: dict[str, float] | None = None, hang: set[str] | None = None,
                 fail: set[str] | None = None, offline: set[str] | None = None) -> None:
        super().__init__(sites, hub)
        self.offline = set(offline or ())
        self.paths = {}
        for s in sites:
            if s in self.offline:
                continue
            p = site_path(s, data_dir)
            self.paths[s] = p if p.exists() else materialize(s, data_dir)
        self.delay_s = delay_s
        self.site_delay = dict(site_delay or {})
        self.hang = set(hang or ())
        self.fail = set(fail or ())
        self._data = {}

    def site_status(self) -> dict[str, str]:
        return {s: "offline" if s in self.offline or not self.paths[s].exists() else "online" for s in self.sites}

    def _wait(self, task, site: str, seconds: float) -> bool:
        """Sleep, but give up early when the site or the task is cancelled; True if still wanted."""
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if task.cancelled or task.site_cancelled(site):
                return False
            time.sleep(0.02)
        return not (task.cancelled or task.site_cancelled(site))

    def run_round(self, task, weights, client_config):
        for site in task.sites:
            if task.cancelled:
                raise FrameworkError("cancelled")
            if task.site_cancelled(site):
                continue
            if site in self.offline:
                self.report_site_error(task, site, f"mock: site {site} is offline")
                continue
            if site in self.hang:
                while self._wait(task, site, 0.5) and not task.deadline_exceeded():
                    pass
                continue
            if not self._wait(task, site, self.delay_s + self.site_delay.get(site, 0.0)):
                continue
            if site in self.fail:
                self.report_site_error(task, site, f"mock: site {site} failed on purpose")
                continue
            if site not in self._data:
                self._data[site] = load_site(self.paths[site])
            new, n, metrics = train(weights, self._data[site], client_config)
            self.report_site(task, SiteResult(site, n, metrics, flhub_core.code_version(), weights=new))
            task.done += 1
        return []


def _site_values(items: list[str]) -> dict[str, float]:
    out = {}
    for item in items:
        site, _, value = item.partition("=")
        out[site] = float(value)
    return out


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m adapters.mock")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8819)
    ap.add_argument("--sites", nargs="+", default=sorted(SITES, key=SITES.get))
    ap.add_argument("--data-dir", default=None)
    ap.add_argument("--hub", default=os.environ.get("FLHUB_HUB_URL"))
    ap.add_argument("--delay", type=float, default=0.0, help="extra seconds per site (to test polling)")
    ap.add_argument("--site-delay", nargs="*", default=[], metavar="SITE=S", help="extra seconds for one site")
    ap.add_argument("--hang", nargs="*", default=[], metavar="SITE", help="sites that never finish")
    ap.add_argument("--fail", nargs="*", default=[], metavar="SITE", help="sites that fail")
    ap.add_argument("--offline", nargs="*", default=[], metavar="SITE", help="registered but offline sites")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    MockAdapter(args.sites, args.data_dir, HubClient(args.hub), args.delay, site_delay=_site_values(args.site_delay),
                hang=set(args.hang), fail=set(args.fail), offline=set(args.offline)).serve(args.host, args.port)

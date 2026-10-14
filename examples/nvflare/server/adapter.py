"""Starts the hub's NVFlare adapter next to a real NVFlare server (example).

How the adapter side is built from the library:

  RelayAdapter (adapters/common/relay.py)   the generic part: flhub JSON-RPC for the orchestrator
                                            (discover, dispatch, tasks/get, collect, cancel) and the
                                            /relay/* routes that the relay inside NVFlare long-polls
  NVFlareAdapter (adapters/nvflare/service.py)  adds what is specific to NVFlare:
      - site status: a site is online only if the relay sees it in the relay job AND the FLARE API
        probe shows that client running the job (NVFlare keeps dead clients "connected" for minutes);
      - a keeper thread that submits the relay job, adopts a running one, and restarts it between
        rounds when a restarted client is outside it (NVFlare does not readmit it into a running job).
  FlareAdmin (adapters/nvflare/flare_admin.py)  the FLARE API session, opened with an admin startup
                                            kit that may submit jobs with custom code.

This file only wires the library classes together, as `python -m adapters.nvflare.service` does.
With an NVFlare server, its admin kit and an exported relay job (relay_job.py export):

    python examples/nvflare/server/adapter.py --admin-kit <admin kit dir> --job-dir <dir>/flhub-relay \
        --sites nvf-h1 nvf-h2 [--port 8811] [--hub-url http://127.0.0.1:8800]

Without an NVFlare server: `--check` starts the same adapter on a free port with a stand-in for the
FLARE API, prints /health and discover (every site offline: no relay, no client), and exits.
run_local.py runs the relay itself in NVFlare's simulator.
"""

from __future__ import annotations

import argparse
import json
import signal
import tempfile
import threading
import urllib.request

from adapters.common.hubclient import HubClient
from adapters.nvflare.flare_admin import FlareAdmin
from adapters.nvflare.service import NVFlareAdapter


def make_adapter(sites: list[str], admin, job_dir: str, hub_url: str | None) -> NVFlareAdapter:
    """The adapter object: the site ids are the NVFlare client names; the hub core stores the weights."""
    return NVFlareAdapter(sites, admin, job_dir, HubClient(hub_url))


class NoServerAdmin:
    """Stands in for FlareAdmin in --check mode: no NVFlare server, so no jobs and no connected clients."""

    username = "check"

    def active_jobs(self, name):
        return []

    def client_jobs(self):
        return {}

    def close(self):
        pass


def check(sites: list[str], hub_url: str | None) -> int:
    """Starts the adapter without an NVFlare server, prints /health and discover, and stops it."""
    with tempfile.TemporaryDirectory(prefix="flhub-check-") as job_dir:
        return _check(make_adapter(sites, NoServerAdmin(), job_dir, hub_url), sites)


def _check(adapter: NVFlareAdapter, sites: list[str]) -> int:
    server = adapter.build_server("127.0.0.1", 0).start()  # a free port on localhost
    try:
        with urllib.request.urlopen(server.url + "/health", timeout=10) as r:
            health = json.load(r)
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "discover", "params": {"protocol": "flhub/0.2"}})
        request = urllib.request.Request(server.url + "/rpc", data=body.encode(), headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=10) as r:
            discover = json.load(r)["result"]
        adapter.keep_once()  # no client is connected, so the keeper submits no relay job
        sites_seen = {s["site_id"]: s["status"] for s in discover["sites"]}
        print(f"health   {health}\ndiscover adapter={discover['adapter']} framework {discover['framework_version']} "
              f"protocol {discover['protocol']} sites {sites_seen}\nkeeper   relay job submitted: {adapter.relay_job_id}")
        ok = health.get("ok") and set(sites_seen) == set(sites) and adapter.relay_job_id is None
        print("check:", "PASS" if ok else "FAIL")
        return 0 if ok else 1
    finally:
        server.stop()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--admin-kit", help="NVFlare admin startup kit directory")
    ap.add_argument("--job-dir", help="exported relay job folder (relay_job.py export)")
    ap.add_argument("--sites", nargs="+", default=["nvf-h1", "nvf-h2"], help="NVFlare client names = hub site ids")
    ap.add_argument("--port", type=int, default=8811)
    ap.add_argument("--hub-url", default=None, help="hub core URL (default: $FLHUB_HUB_URL or 127.0.0.1:8800)")
    ap.add_argument("--check", action="store_true", help="run without an NVFlare server: /health + discover, then exit")
    args = ap.parse_args()
    if args.check:
        return check(args.sites, args.hub_url)
    if not args.admin_kit or not args.job_dir:
        ap.error("--admin-kit and --job-dir are required (or use --check)")

    admin = FlareAdmin(args.admin_kit)  # logs in to the NVFlare server's admin port (8003 on the testbed)
    adapter = make_adapter(args.sites, admin, args.job_dir, args.hub_url)
    server = adapter.build_server("127.0.0.1", args.port).start()  # JSON-RPC + /relay/* on localhost only
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    threading.Thread(target=adapter.keep_relay_job, args=(stop,), daemon=True).start()  # the job keeper
    print(f"NVFlare adapter on {server.url} for {args.sites}", flush=True)
    stop.wait()
    adapter.shutdown_relay()  # ask the relay to end its job; abort it if it does not
    server.stop()
    admin.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

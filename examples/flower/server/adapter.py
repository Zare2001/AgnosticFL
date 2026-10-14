"""The Flower adapter, annotated (example): the hub-facing service next to a Flower SuperLink.

How the adapter side is built (all classes come from the library):
- BaseAdapter (adapters/common/base.py) serves the hub's protocol (flhub/0.2, JSON-RPC: discover,
  dispatch, tasks/get, collect, cancel). A framework that can be driven from outside implements
  run_round() itself; vantage6 does, its adapter is a vantage6 user that creates tasks.
- RelayAdapter (adapters/common/relay.py) is for frameworks whose round must run inside the
  framework server: run_round() queues the round, and a relay inside the framework takes it
  with GET /relay/next and posts each site's result back. NVFlare's relay is a job, Flower's a
  ServerApp (server_app.py).
- FlowerAdapter (adapters/flower/service.py) is a RelayAdapter plus Flower's parts: stage_fab()
  packs the Flower app and flhub_core into one FAB, and RelayLauncher keeps one relay run alive
  with `flwr run`, replacing it when its beats stop.
- The hub core is only the adapter's blob store (HubClient): the relay reads the global model
  there and stores each site's weights there. Nothing registers with the hub core: the
  orchestrator finds the adapter through its run configuration (adapters: name, url, sites).

  python examples/flower/server/adapter.py --check     # no SuperLink needed: /health + discover
  python examples/flower/server/adapter.py --hub http://127.0.0.1:8800 --flwr-home <dir> \
      [--port 8813] [--sites flw-h5 flw-h6] [--app-dir clients/flower]
"""

from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

from adapters.common import AdapterClient, HubClient
from adapters.flower.service import FlowerAdapter, RelayLauncher, stage_fab

REPO = Path(__file__).resolve().parents[3]


def start(sites: list[str], hub_url: str, port: int, app_dir: Path, flwr_home: Path, connection: str,
          launch: bool = True):
    """Start the adapter service; with launch=True also the thread that keeps a relay run alive."""
    # 1. The FAB: the Flower app (hospital ClientApp + relay ServerApp) plus the hub's flhub_core,
    #    staged into one directory next to FLWR_HOME. Its content id, the fab id, lets the adapter
    #    tell the current relay from one left over from an older FAB.
    fab_dir = flwr_home.parent / "fab-src"
    fab_id = stage_fab(app_dir, REPO / "flhub_core" / "src" / "flhub_core", fab_dir)
    # 2. The adapter: flhub JSON-RPC and the /relay/* routes on one local port; the hub core for blobs.
    adapter = FlowerAdapter(sites, HubClient(hub_url), fab_id=fab_id)
    server = adapter.build_server("127.0.0.1", port).start()
    # 3. The relay launcher: whenever no relay is alive, `flwr run <fab dir> <connection>` with the
    #    adapter's URL in the run config. The connection (address of the SuperLink's HTTP API) is in
    #    $FLWR_HOME/config.toml, see config.toml next to this file.
    stop = threading.Event()
    launcher = None
    if launch:
        run_config = f"adapter-url='{server.url}' hub-url='{hub_url}' fab-id='{fab_id}'"
        flwr_bin = str(Path(sys.executable).with_name("flwr"))
        launcher = RelayLauncher(adapter, flwr_bin, str(fab_dir), str(flwr_home), connection, run_config)
        threading.Thread(target=launcher.loop, args=(stop,), daemon=True).start()
    return adapter, server, launcher, stop


def check() -> int:
    """Start the adapter without a relay on a free port, call /health and discover, stop it."""
    with tempfile.TemporaryDirectory(prefix="flhub-flower-check-") as tmp:
        adapter, server, _, _ = start(["flw-h5", "flw-h6"], "http://127.0.0.1:8800", 0,
                                      REPO / "clients" / "flower", Path(tmp) / "flwr-home", "flhub",
                                      launch=False)
        try:
            health = json.load(urllib.request.urlopen(server.url + "/health", timeout=10))
            disc = AdapterClient(server.url).discover()
        finally:
            server.stop()
    print("health:", json.dumps(health))
    print("discover:", disc["adapter"], disc["framework"], disc["framework_version"], disc["protocol"],
          {s["site_id"]: s["status"] for s in disc["sites"]})
    # Without a live relay the adapter cannot reach any SuperNode, so every site is "unknown".
    return 0 if health.get("ok") and all(s["status"] == "unknown" for s in disc["sites"]) else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="examples/flower/server/adapter.py")
    ap.add_argument("--check", action="store_true", help="dry run without Flower: /health and discover")
    ap.add_argument("--port", type=int, default=8813)
    ap.add_argument("--sites", nargs="+", default=["flw-h5", "flw-h6"], help="hub site ids (= SuperNode site-id)")
    ap.add_argument("--hub", default="http://127.0.0.1:8800", help="hub core URL")
    ap.add_argument("--app-dir", type=Path, default=REPO / "clients" / "flower", help="Flower app for the FAB")
    ap.add_argument("--flwr-home", type=Path, default=Path.home() / "flhub-runtime/flower/flwr-home")
    ap.add_argument("--connection", default="flhub", help="connection name in $FLWR_HOME/config.toml")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args.check:
        return check()
    adapter, server, launcher, stop = start(args.sites, args.hub, args.port, args.app_dir, args.flwr_home,
                                            args.connection)
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    print(f"flower adapter on {server.url}, sites {args.sites}, hub {args.hub}", flush=True)
    while not stop.wait(1.0):
        pass
    # On stop: a live relay takes a shutdown at its next poll and ends its run; if it does not
    # within 15 s, stop the runs this adapter started with `flwr stop`.
    attached = adapter.relay_alive()
    adapter.request_relay_shutdown()
    end = time.monotonic() + (15.0 if attached else 0.0)
    while not adapter.shutdown_delivered() and time.monotonic() < end:
        time.sleep(0.2)
    server.stop()
    if not adapter.shutdown_delivered():
        launcher.stop_runs()
    return 0


if __name__ == "__main__":
    sys.exit(main())

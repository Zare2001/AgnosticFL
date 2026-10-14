"""Start the vantage6 adapter from your own Python code: the server side of flhub for vantage6.

The vantage6 adapter (adapters/vantage6/service.py) is a BaseAdapter. A vantage6 *user* can create
tasks and read their results through the server's API, so nothing has to run inside the vantage6
server; NVFlare and Flower need such a relay (RelayAdapter, see docs/adding-a-framework.md). This
script builds the real adapter from settings and serves the flhub protocol; it adds nothing to it.

    export V6_ADAPTER_PASSWORD=...    # the password of the adapter's vantage6 user; never on the command line
    python examples/vantage6/server/run_adapter.py --server-url http://<central-host> \
        --image <registry-host>:5050/flhub/v6-example:<tag> --sites v6-h3 v6-h4

    python examples/vantage6/server/run_adapter.py --check
        serves on a free port, asks /health and `discover` as the orchestrator does, and stops; needs
        no vantage6 server (the sites then report "unknown")

The orchestrator finds the adapter through its run configuration (run-config.yaml next to this file).
The adapter itself talks only to the vantage6 server and to the hub core.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import socket
import sys
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(REPO), str(REPO / "flhub_core" / "src")]  # the hub package of this checkout

from adapters.common import AdapterClient, HubClient  # noqa: E402  flhub: protocol client, hub-core client
from adapters.vantage6.service import Vantage6Adapter  # noqa: E402  flhub: the vantage6 mapping (a BaseAdapter)


def build_adapter(args: argparse.Namespace) -> Vantage6Adapter:
    """The adapter and its settings: the same object that `python -m adapters.vantage6` builds."""
    return Vantage6Adapter(
        args.sites,  # flhub site ids = the names of the hospitals' vantage6 organisations
        server_url=args.server_url, server_port=args.server_port, api_path=args.api_path,  # the vantage6 server
        username=args.username, password=os.environ.get("V6_ADAPTER_PASSWORD", ""),  # the adapter's vantage6 user
        collaboration=args.collaboration,  # unencrypted, with the user's organisation as a participant
        image=args.image,  # the algorithm image each task runs (examples/vantage6/client/Dockerfile)
        hub=HubClient(args.hub),  # hub core: the adapter reads each round's model and stores each site's update
    )


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def check(adapter: Vantage6Adapter) -> int:
    """Serve on a free port and ask what the orchestrator asks first: /health and `discover`."""
    server = adapter.build_server("127.0.0.1", 0).start()  # JSON-RPC: discover, dispatch, tasks/get, collect, cancel
    try:
        with urllib.request.urlopen(server.url + "/health", timeout=10) as reply:
            health = json.loads(reply.read())
        found = AdapterClient(server.url).discover()  # protocol flhub/0.2, as the orchestrator calls it
    finally:
        server.stop()
    print(f"adapter on {server.url}: /health {json.dumps(health)}")
    print(f"discover: adapter {found['adapter']}, framework {found['framework']} {found['framework_version']}, "
          f"protocol {found['protocol']} (supports {', '.join(found['protocols'])})")
    print("  sites: " + ", ".join(f"{s['site_id']}={s['status']}" for s in found["sites"]))
    print("  capabilities: " + json.dumps(found["capabilities"], sort_keys=True))
    ok = health.get("ok") is True and [s["site_id"] for s in found["sites"]] == adapter.sites
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="run_adapter.py", description="Run the vantage6 adapter of flhub.")
    ap.add_argument("--host", default="127.0.0.1", help="JSON-RPC bind address (keep the adapter local)")
    ap.add_argument("--port", type=int, default=8812, help="JSON-RPC port; the run configuration names it")
    ap.add_argument("--sites", nargs="+", default=["v6-h3", "v6-h4"], help="vantage6 organisation names")
    ap.add_argument("--server-url", default="http://127.0.0.1")
    ap.add_argument("--server-port", type=int, default=7601)
    ap.add_argument("--api-path", default="/api")
    ap.add_argument("--collaboration", default="flhub")
    ap.add_argument("--username", default="hub-adapter")
    ap.add_argument("--image", help="algorithm image the nodes pull, <registry>/flhub/v6-example:<tag>")
    ap.add_argument("--hub", default="http://127.0.0.1:8800", help="hub core URL")
    ap.add_argument("--check", action="store_true", help="serve on a free port, call /health and discover, stop")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.WARNING if args.check else logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    if args.check:
        args.server_port = free_port()  # nothing listens there: the check runs without a vantage6 server
        args.image = args.image or "example/v6-example:check"
        return check(build_adapter(args))
    if not args.image or not os.environ.get("V6_ADAPTER_PASSWORD"):
        ap.error("--image and the environment variable V6_ADAPTER_PASSWORD are required")
    build_adapter(args).serve(args.host, args.port)  # blocks until interrupted
    return 0


if __name__ == "__main__":
    sys.exit(main())

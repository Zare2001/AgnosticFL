"""Flower adapter service: flhub/0.1 JSON-RPC + relay routes on 127.0.0.1:8813.

Built on adapters.common.RelayAdapter. The relay is the ServerApp of the Flower app in
clients/flower (flhub_flower.server_app); it runs inside one long-lived Flower run on the
local SuperLink. This service keeps exactly one relay run alive: whenever no relay has been
heard from for RELAY_STALE_S it submits a new run with ``flwr run <app-dir> <connection>``
(SuperLink HTTP API on 127.0.0.1:9093) and stops the relay runs it started before.

    python -m adapters.flower.service [--port 8813] [--sites flw-h5 flw-h6] [--hub URL]
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import signal
import subprocess
import sys
import threading
import time
from importlib.metadata import version
from pathlib import Path

from adapters.common.hubclient import DEFAULT_HUB_URL, HubClient
from adapters.common.relay import RelayAdapter

log = logging.getLogger("flhub.adapter.flower")

REPO = Path(__file__).resolve().parents[2]
RELAY_STALE_S = 45.0  # no poll and no progress for this long: the relay is gone
ADOPT_GRACE_S = 20.0  # at start-up, let an already running relay re-attach first
LAUNCH_COOLDOWN_S = 120.0  # minimum time between two relay launches
SHUTDOWN_WAIT_S = 15.0  # on stop, wait this long for the relay to take its shutdown


class FlowerAdapter(RelayAdapter):
    adapter_name = "flower"
    framework = "flower"
    framework_version = version("flwr")

    def __init__(self, sites: list[str], hub: HubClient | None = None) -> None:
        super().__init__(sites, hub)
        self._last_progress = 0.0

    def _route_progress(self, req):
        # Progress posts double as the relay's heartbeat while a round is running.
        self._last_progress = time.monotonic()
        return super()._route_progress(req)

    def relay_alive(self) -> bool:
        last = max(self._last_poll, self._last_progress)
        return bool(last) and time.monotonic() - last < RELAY_STALE_S

    def shutdown_delivered(self) -> bool:
        return not self._shutdown.is_set()


class RelayLauncher:
    """Starts ``flwr run`` whenever no relay is alive; stops the runs it replaced."""

    def __init__(self, adapter: FlowerAdapter, flwr_bin: str, app_dir: str, flwr_home: str,
                 connection: str, run_config: str) -> None:
        self.adapter = adapter
        self.flwr_bin = flwr_bin
        self.connection = connection
        self.cmd = [flwr_bin, "run", app_dir, connection, "--run-config", run_config]
        self.env = {**os.environ, "FLWR_HOME": flwr_home}
        self.last_launch = float("-inf")
        self.run_ids: list[str] = []

    def loop(self, stop: threading.Event) -> None:
        started = time.monotonic()
        while not stop.wait(5.0):
            now = time.monotonic()
            if self.adapter.relay_alive() or now - started < ADOPT_GRACE_S:
                continue
            if now - self.last_launch >= LAUNCH_COOLDOWN_S:
                self.launch()

    def launch(self) -> None:
        self.last_launch = time.monotonic()
        log.info("no live relay, starting one: %s", " ".join(self.cmd))
        try:
            proc = subprocess.run(self.cmd, env=self.env, capture_output=True, text=True, timeout=180)
        except (OSError, subprocess.TimeoutExpired) as exc:
            log.error("flwr run failed: %s", exc)
            return
        out = (proc.stdout + proc.stderr).strip()
        match = re.search(r"started run (\d+)", out, re.IGNORECASE)
        if proc.returncode != 0 or not match:
            log.error("flwr run exit=%s: %s", proc.returncode, out[-2000:])
            return
        old, self.run_ids = self.run_ids, [match.group(1)]
        log.info("relay run %s submitted", match.group(1))
        self.stop_runs(old)  # relays we started earlier that went silent

    def stop_runs(self, run_ids: list[str] | None = None) -> None:
        for run_id in self.run_ids if run_ids is None else run_ids:
            proc = subprocess.run([self.flwr_bin, "stop", run_id, self.connection], env=self.env,
                                  capture_output=True, text=True, timeout=60, check=False)
            log.info("flwr stop %s: exit=%s %s", run_id, proc.returncode,
                     (proc.stdout + proc.stderr).strip()[-300:])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m adapters.flower.service")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8813)
    ap.add_argument("--sites", nargs="+", default=["flw-h5", "flw-h6"])
    ap.add_argument("--hub", default=os.environ.get("FLHUB_HUB_URL", DEFAULT_HUB_URL), help="hub core URL")
    ap.add_argument("--app-dir", default=str(REPO / "clients" / "flower"), help="Flower app (FAB) dir")
    ap.add_argument("--flwr-home", default=str(Path.home() / "flhub-runtime/flower/flwr-home"))
    ap.add_argument("--connection", default="flhub", help="SuperLink connection in $FLWR_HOME/config.toml")
    ap.add_argument("--no-launch", action="store_true", help="never start a relay run (tests)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    adapter = FlowerAdapter(args.sites, HubClient(args.hub))
    server = adapter.build_server(args.host, args.port).start()
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    launcher = None
    if not args.no_launch:
        run_config = f"adapter-url='{server.url}' hub-url='{args.hub}'"
        launcher = RelayLauncher(adapter, str(Path(sys.executable).with_name("flwr")), args.app_dir,
                                 args.flwr_home, args.connection, run_config)
        threading.Thread(target=launcher.loop, args=(stop,), name="relay-launcher", daemon=True).start()
    log.info("flower adapter on %s, sites %s, hub %s", server.url, args.sites, args.hub)
    while not stop.wait(1.0):
        pass

    log.info("stopping: asking the relay to shut down")
    attached = adapter.relay_alive()
    adapter.request_relay_shutdown()
    end = time.monotonic() + (SHUTDOWN_WAIT_S if attached else 0.0)
    while not adapter.shutdown_delivered() and time.monotonic() < end:
        time.sleep(0.2)
    delivered = adapter.shutdown_delivered()
    server.stop()
    if launcher is not None and not delivered:
        launcher.stop_runs()
    log.info("stopped (relay shutdown %s)", "delivered" if delivered else "not delivered")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Flower adapter service: flhub JSON-RPC + relay routes on 127.0.0.1:8813.

Built on adapters.common.RelayAdapter. The relay is the ServerApp of the Flower app in
clients/flower (flhub_flower.server_app); it runs inside one long-lived Flower run on the
local SuperLink. This service keeps exactly one relay run alive:

- FAB: at start-up the service stages clients/flower plus the flhub_core package into one
  directory and submits that with ``flwr run <dir> <connection>`` (SuperLink HTTP API on
  127.0.0.1:9093, per ADR-007). The FAB thus carries the training code to the hospital
  SuperNodes, Flower's own code-distribution channel (ADR-016). The staged content id
  (``fab-id``) goes to the relay as run config and comes back with its polls and beats; a relay
  with another fab id (left from a rollout) is told to leave and is never handed a job.
- Liveness: the relay posts /relay/beat from a thread every few seconds. When no poll,
  progress or beat arrived for RELAY_STALE_S (e.g. the SuperLink restarted and took the relay
  run with it), the rounds that relay had taken fail at once and a new relay run is submitted.

    python -m adapters.flower.service [--port 8813] [--sites flw-h5 flw-h6] [--hub URL]
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import os
import re
import shutil
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
RELAY_STALE_S = 20.0  # no poll, progress or beat for this long: the relay is gone
ADOPT_GRACE_S = 15.0  # at start-up, let an already running relay re-attach first
LAUNCH_COOLDOWN_S = 60.0  # after a submitted relay run, give it this long to start polling
LAUNCH_RETRY_S = 10.0  # after a failed `flwr run` (e.g. SuperLink still down), retry this soon
SHUTDOWN_WAIT_S = 15.0  # on stop, wait this long for the relay to take its shutdown


class FlowerAdapter(RelayAdapter):
    adapter_name = "flower"
    framework = "flower"
    framework_version = version("flwr")
    # Weights travel as hub-core blobs and as 5 MiB Flower array chunks (gRPC limit 2 GiB per
    # message), so the adapter itself imposes no payload cap below the E4a sweep.
    capabilities = {**RelayAdapter.capabilities, "max_payload_mb": 2048}

    def __init__(self, sites: list[str], hub: HubClient | None = None, fab_id: str | None = None) -> None:
        super().__init__(sites, hub)
        self.fab_id = fab_id
        self.relay_info: dict = {}
        self._last_progress = 0.0
        self._last_beat = 0.0
        self._taken: set[str] = set()  # jobs a relay has taken and not answered yet

    def extra_routes(self, server) -> None:
        super().extra_routes(server)
        server.route("POST", "/relay/beat", self._route_beat)

    def stale(self, fab: str | None) -> bool:
        """A relay that carries another FAB than this adapter staged (e.g. left from a rollout)."""
        return bool(fab and self.fab_id and fab != self.fab_id)

    def _route_next(self, req):
        fab = req.query.get("fab")
        if self.stale(fab):  # only that relay leaves; it gets no job and no liveness credit
            log.warning("relay with fab %s polled, this adapter serves fab %s: told it to leave", fab, self.fab_id)
            return 200, {"type": "shutdown"}
        status, body = super()._route_next(req)
        if body.get("type") == "train":
            with self._lock:
                self._taken.add(body["job_id"])
        return status, body

    def _route_progress(self, req):
        # Progress posts double as the relay's heartbeat while a round is running.
        self._last_progress = time.monotonic()
        return super()._route_progress(req)

    def _route_beat(self, req):
        body = req.json() or {}
        if self.stale(body.get("fab_id")):
            log.warning("relay run %s carries fab %s, this adapter serves fab %s: telling it to leave",
                        body.get("run_id"), body.get("fab_id"), self.fab_id)
            return 200, {"ok": True, "fab_id": self.fab_id, "leave": True}
        self._last_beat = time.monotonic()
        self.relay_info = {k: body.get(k) for k in ("fab_id", "run_id", "job_id", "code_version")}
        return 200, {"ok": True, "fab_id": self.fab_id}

    def _route_status(self, req):
        status, body = super()._route_status(req)
        return status, {**body, "relay_alive": self.relay_alive(), "fab_id": self.fab_id, "relay": self.relay_info}

    def site_status(self) -> dict[str, str]:
        # A lost relay's last site report is stale: without a live relay nothing can be dispatched.
        return super().site_status() if self.relay_alive() else {}

    def relay_alive(self) -> bool:
        last = max(self._last_poll, self._last_progress, self._last_beat)
        return bool(last) and time.monotonic() - last < RELAY_STALE_S

    def shutdown_delivered(self) -> bool:
        return not self._shutdown.is_set()

    def fail_taken_jobs(self, reason: str) -> list[str]:
        """Fail the rounds a lost relay had taken: no result can come back for them."""
        failed = []
        with self._lock:
            for job_id in sorted(self._taken):
                pending = self._pending.get(job_id)
                if pending is not None and not pending.event.is_set():
                    pending.error = reason
                    pending.event.set()
                    failed.append(job_id)
            self._taken.clear()
        return failed


def stage_fab(app_dir: Path, core_dir: Path, out: Path) -> str:
    """Copy the Flower app and the flhub_core package into one FAB source dir.

    Returns a content id (sha256 prefix over relative paths + bytes of all staged files).
    """
    tmp = out.with_name(out.name + ".tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    skip = shutil.ignore_patterns("__pycache__", "*.pyc", "*.egg-info")
    shutil.copytree(app_dir, tmp, ignore=skip)
    shutil.copytree(core_dir, tmp / "flhub_core", ignore=skip)
    h = hashlib.sha256()
    for f in sorted(p for p in tmp.rglob("*") if p.is_file()):
        h.update(f.relative_to(tmp).as_posix().encode() + b"\0" + f.read_bytes() + b"\0")
    shutil.rmtree(out, ignore_errors=True)
    tmp.rename(out)
    return h.hexdigest()[:12]


class RelayLauncher:
    """Starts ``flwr run`` whenever no relay is alive; stops the runs it replaced."""

    def __init__(self, adapter: FlowerAdapter, flwr_bin: str, app_dir: str, flwr_home: str,
                 connection: str, run_config: str) -> None:
        self.adapter = adapter
        self.flwr_bin = flwr_bin
        self.connection = connection
        self.cmd = [flwr_bin, "run", app_dir, connection, "--run-config", run_config]
        self.env = {**os.environ, "FLWR_HOME": flwr_home}
        self.next_launch = 0.0
        self.run_ids: list[str] = []

    def loop(self, stop: threading.Event) -> None:
        started = time.monotonic()
        while not stop.wait(1.0):
            if self.adapter.relay_alive():
                continue
            lost = self.adapter.fail_taken_jobs(
                f"flower relay run lost: no heartbeat for {RELAY_STALE_S:.0f} s (SuperLink restarted?)")
            if lost:
                log.warning("relay lost; failed its rounds %s", lost)
            now = time.monotonic()
            if now - started >= ADOPT_GRACE_S and now >= self.next_launch:
                self.launch()

    def launch(self) -> None:
        log.info("no live relay, starting one: %s", " ".join(self.cmd))
        self.next_launch = time.monotonic() + LAUNCH_RETRY_S
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
        self.next_launch = time.monotonic() + LAUNCH_COOLDOWN_S
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
    ap.add_argument("--app-dir", default=str(REPO / "clients" / "flower"), help="Flower app dir")
    ap.add_argument("--core-dir", default=str(REPO / "flhub_core" / "src" / "flhub_core"),
                    help="flhub_core package dir shipped inside the FAB")
    ap.add_argument("--flwr-home", default=str(Path.home() / "flhub-runtime/flower/flwr-home"))
    ap.add_argument("--connection", default="flhub", help="SuperLink connection in $FLWR_HOME/config.toml")
    ap.add_argument("--no-launch", action="store_true", help="never start a relay run (tests)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    fab_dir = Path(args.flwr_home).parent / "fab-src"
    fab_id = stage_fab(Path(args.app_dir), Path(args.core_dir), fab_dir)
    log.info("FAB source %s staged from %s + %s, fab id %s", fab_dir, args.app_dir, args.core_dir, fab_id)
    adapter = FlowerAdapter(args.sites, HubClient(args.hub), fab_id=fab_id)
    server = adapter.build_server(args.host, args.port).start()
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    launcher = None
    if not args.no_launch:
        run_config = f"adapter-url='{server.url}' hub-url='{args.hub}' fab-id='{fab_id}'"
        launcher = RelayLauncher(adapter, str(Path(sys.executable).with_name("flwr")), str(fab_dir),
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

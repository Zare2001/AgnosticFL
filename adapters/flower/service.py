"""Flower adapter service: flhub/0.1 JSON-RPC on 127.0.0.1:8813 plus relay endpoints.

The relay ServerApp (clients/flower/flhub_flower/server_app.py) runs inside one
long-lived Flower run on the local SuperLink. This service queues dispatched rounds
for it (``POST /relay/next``), turns its per-site results into envelopes
(``POST /relay/result``) and keeps exactly one relay run alive with
``flwr run <app-dir> <connection>`` against the SuperLink's local control API.

    python -m adapters.flower.service --port 8813 --sites flw-h5,flw-h6
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import signal
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.metadata import version
from pathlib import Path
from typing import Any

from adapters.flower.tasks import PROTOCOL, RpcError, TaskBook

log = logging.getLogger("flhub.adapter.flower")

REPO = Path(__file__).resolve().parents[2]
ADOPT_GRACE_S = 15.0  # at start-up, let an already running relay re-attach first
LAUNCH_COOLDOWN_S = 90.0  # minimum time between two relay launches
CAPABILITIES = {
    "payload": "full_weights",
    "dtypes": ["float32"],
    "max_payload_mb": 64,
    "evaluate": False,
    "min_round_interval_s": 0,
}


class RelayLauncher:
    """Starts ``flwr run`` whenever no relay has polled recently."""

    def __init__(self, book: TaskBook, flwr_bin: str, app_dir: str, flwr_home: str,
                 connection: str, run_config: str | None) -> None:
        self.book = book
        self.flwr_bin = flwr_bin
        self.connection = connection
        self.cmd = [flwr_bin, "run", app_dir, connection]
        if run_config:
            self.cmd += ["--run-config", run_config]
        self.env = {**os.environ, "FLWR_HOME": flwr_home}
        self.last_launch = float("-inf")
        self.run_ids: list[str] = []

    def loop(self, stop: threading.Event) -> None:
        started = time.monotonic()
        while not stop.wait(5.0):
            now = time.monotonic()
            if self.book.relay_alive() or now - started < ADOPT_GRACE_S:
                continue
            if now - self.last_launch < LAUNCH_COOLDOWN_S:
                continue
            self.launch()

    def launch(self) -> None:
        self.last_launch = time.monotonic()
        log.info("no live relay, starting one: %s", " ".join(self.cmd))
        try:
            proc = subprocess.run(self.cmd, env=self.env, capture_output=True, text=True,
                                  timeout=180)
        except (OSError, subprocess.TimeoutExpired) as exc:
            log.error("flwr run failed: %s", exc)
            return
        out = (proc.stdout + proc.stderr).strip()
        match = re.search(r"started run (\d+)", out, re.IGNORECASE)
        if proc.returncode != 0 or not match:
            log.error("flwr run exit=%s: %s", proc.returncode, out[-2000:])
            return
        self.run_ids.append(match.group(1))
        log.info("relay run %s submitted", match.group(1))

    def stop_runs(self) -> None:
        """Best effort: stop every relay run this adapter started."""
        for run_id in self.run_ids:
            subprocess.run([self.flwr_bin, "stop", run_id, self.connection], env=self.env,
                           capture_output=True, text=True, timeout=60, check=False)


class Adapter:
    """flhub/0.1 method table on top of the task book."""

    def __init__(self, book: TaskBook, hub_url: str) -> None:
        self.book = book
        self.hub_url = hub_url.rstrip("/")
        self.methods = {
            "discover": self.discover,
            "dispatch": lambda p: self.book.dispatch(p, self.hub_url),
            "tasks/get": lambda p: self.book.get(str(p["task_id"])),
            "collect": lambda p: self.book.collect(str(p["task_id"])),
            "cancel": lambda p: self.book.cancel(str(p["task_id"])),
        }

    def discover(self, _params: dict[str, Any]) -> dict[str, Any]:
        return {
            "adapter": "flower",
            "framework": "flower",
            "framework_version": version("flwr"),
            "protocol": PROTOCOL,
            "sites": self.book.site_status(),
            "capabilities": CAPABILITIES,
        }

    def handle(self, req: Any) -> dict[str, Any]:
        rid = req.get("id") if isinstance(req, dict) else None
        try:
            if not isinstance(req, dict) or req.get("jsonrpc") != "2.0":
                raise RpcError(-32600, "invalid JSON-RPC 2.0 request")
            method, params = req.get("method"), req.get("params") or {}
            if method not in self.methods:
                raise RpcError(-32601, f"method not found: {method}")
            if not isinstance(params, dict):
                raise RpcError(-32602, "params must be an object")
            if params.get("protocol") != PROTOCOL:
                raise RpcError(-32010, f"unsupported protocol {params.get('protocol')!r}, "
                                       f"this adapter speaks {PROTOCOL}")
            return {"jsonrpc": "2.0", "id": rid, "result": self.methods[method](params)}
        except RpcError as exc:
            err = {"code": exc.code, "message": exc.message}
        except (KeyError, TypeError, ValueError) as exc:
            err = {"code": -32602, "message": f"invalid params: {exc!r}"}
        except Exception as exc:  # noqa: BLE001 - JSON-RPC internal error
            log.exception("internal error")
            err = {"code": -32603, "message": f"internal error: {exc!r}"}
        return {"jsonrpc": "2.0", "id": rid, "error": err}


def make_handler(adapter: Adapter) -> type[BaseHTTPRequestHandler]:
    book = adapter.book

    class Handler(BaseHTTPRequestHandler):
        server_version = "flhub-flower-adapter/0.1"

        def log_message(self, fmt: str, *args: Any) -> None:  # quiet access log
            log.debug("%s " + fmt, self.address_string(), *args)

        def _send(self, code: int, obj: Any) -> None:
            body = json.dumps(obj).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/health":
                self._send(200, {"ok": True, "relay_id": book.relay_id,
                                 "relay_alive": book.relay_alive(),
                                 "sites": book.site_status()})
            else:
                self._send(404, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            try:
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length) or b"null")
            except (ValueError, json.JSONDecodeError):
                if self.path == "/rpc":
                    self._send(200, {"jsonrpc": "2.0", "id": None,
                                     "error": {"code": -32700, "message": "parse error"}})
                else:
                    self._send(400, {"error": "invalid JSON"})
                return
            if self.path == "/rpc":
                self._send(200, adapter.handle(body))
            elif self.path == "/relay/next":
                self._send(200, book.next_job(str(body["relay_id"]), body.get("nodes", []),
                                              float(body.get("wait_s", 5.0))))
            elif self.path == "/relay/result":
                self._send(200, book.relay_result(body))
            else:
                self._send(404, {"error": "not found"})

    return Handler


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8813)
    ap.add_argument("--sites", default="flw-h5,flw-h6", help="comma-separated site ids")
    ap.add_argument("--hub-url", default="http://127.0.0.1:8800", help="hub core (blob store)")
    ap.add_argument("--app-dir", default=str(REPO / "clients" / "flower"))
    ap.add_argument("--flwr-home", default=str(Path.home() / "flhub-runtime/flower/flwr-home"))
    ap.add_argument("--connection", default="flhub", help="SuperLink connection in config.toml")
    ap.add_argument("--run-config", default=None, help="extra flwr run --run-config overrides")
    ap.add_argument("--no-launch", action="store_true", help="never start a relay run (tests)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    book = TaskBook([s for s in args.sites.split(",") if s])
    adapter = Adapter(book, args.hub_url)
    server = ThreadingHTTPServer((args.host, args.port), make_handler(adapter))
    server.daemon_threads = True
    stop = threading.Event()
    launcher = None
    if not args.no_launch:
        flwr_bin = str(Path(sys.executable).with_name("flwr"))
        launcher = RelayLauncher(book, flwr_bin, args.app_dir, args.flwr_home,
                                 args.connection, args.run_config)
        threading.Thread(target=launcher.loop, args=(stop,), daemon=True).start()

    def shutdown(_sig: int, _frm: Any) -> None:
        log.info("stopping: telling the relay to shut down")
        stop.set()
        book.stop()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    log.info("flower adapter on http://%s:%d (sites %s)", args.host, args.port, book.sites)
    server.serve_forever()
    time.sleep(1.0)  # let a polling relay pick up the shutdown answer
    if launcher is not None:
        launcher.stop_runs()
    return 0


if __name__ == "__main__":
    sys.exit(main())

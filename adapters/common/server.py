"""Minimal threaded HTTP server with JSON-RPC 2.0 on POST /rpc plus extra routes (stdlib only).

    srv = RpcServer(port=8811, name="nvflare-adapter")
    srv.method("discover", lambda params: {...})
    srv.route("GET", "/relay/next", handler)      # handler(Request) -> (status, json | bytes)
    srv.route("GET", "/blobs/*", handler)         # trailing * = prefix match
    srv.serve_forever()                           # or srv.start() for a background thread
"""

from __future__ import annotations

import json
import logging
import threading
import traceback
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .protocol import INTERNAL_ERROR, INVALID_PARAMS, INVALID_REQUEST, METHOD_NOT_FOUND, PARSE_ERROR, RpcError

log = logging.getLogger("flhub.server")
MAX_BODY = 512 * 2**20


@dataclass
class Request:
    method: str
    path: str
    query: dict[str, str]
    headers: Any
    body: bytes

    def json(self) -> Any:
        return json.loads(self.body or b"null")


Handler = Callable[[Request], tuple[int, Any]]


class RpcServer:
    def __init__(self, host: str = "127.0.0.1", port: int = 0, name: str = "flhub") -> None:
        self.name = name
        self.methods: dict[str, Callable[[dict], Any]] = {}
        self.routes: dict[tuple[str, str], Handler] = {}
        self.route("GET", "/health", lambda req: (200, {"ok": True, "name": self.name}))
        server = self

        class _Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, fmt, *args):  # route http.server's stderr noise to logging
                log.debug("%s %s", self.address_string(), fmt % args)

            def _serve(self, method: str) -> None:
                parsed = urllib.parse.urlsplit(self.path)
                length = int(self.headers.get("Content-Length") or 0)
                if length > MAX_BODY:
                    return self._send(413, {"error": "body too large"}, method)
                body = self.rfile.read(length) if length else b""
                req = Request(method, parsed.path, dict(urllib.parse.parse_qsl(parsed.query)), self.headers, body)
                try:
                    if method == "POST" and parsed.path == "/rpc":
                        status, payload = 200, server.handle_rpc(body)
                    else:
                        handler = server.find_route("GET" if method == "HEAD" else method, parsed.path)
                        if handler is None:
                            status, payload = 404, {"error": f"no route {method} {parsed.path}"}
                        else:
                            status, payload = handler(req)
                except Exception as exc:  # never kill the server thread
                    log.error("%s %s failed: %s", method, parsed.path, traceback.format_exc())
                    status, payload = 500, {"error": f"{type(exc).__name__}: {exc}"}
                self._send(status, payload, method)

            def _send(self, status: int, payload: Any, method: str) -> None:
                if payload is None:
                    data, ctype = b"", "application/json"
                elif isinstance(payload, (bytes, bytearray, memoryview)):
                    data, ctype = bytes(payload), "application/octet-stream"
                else:
                    data, ctype = json.dumps(payload).encode(), "application/json"
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                if method != "HEAD":
                    self.wfile.write(data)

            def do_GET(self):
                self._serve("GET")

            def do_HEAD(self):
                self._serve("HEAD")

            def do_POST(self):
                self._serve("POST")

            def do_PUT(self):
                self._serve("PUT")

            def do_DELETE(self):
                self._serve("DELETE")

        self.httpd = ThreadingHTTPServer((host, port), _Handler)
        self.httpd.daemon_threads = True
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        host, port = self.httpd.server_address[:2]
        return f"http://{host}:{port}"

    def method(self, name: str, fn: Callable[[dict], Any]) -> None:
        self.methods[name] = fn

    def route(self, method: str, path: str, fn: Handler) -> None:
        self.routes[(method.upper(), path)] = fn

    def find_route(self, method: str, path: str) -> Handler | None:
        if (method, path) in self.routes:
            return self.routes[(method, path)]
        for (m, p), fn in self.routes.items():
            if m == method and p.endswith("*") and path.startswith(p[:-1]):
                return fn
        return None

    def handle_rpc(self, body: bytes) -> dict | None:
        try:
            req = json.loads(body)
        except (ValueError, UnicodeDecodeError) as exc:
            return _error(None, RpcError(PARSE_ERROR, f"parse error: {exc}"))
        if not isinstance(req, dict) or req.get("jsonrpc") != "2.0" or not isinstance(req.get("method"), str):
            return _error(req.get("id") if isinstance(req, dict) else None,
                          RpcError(INVALID_REQUEST, "expected a JSON-RPC 2.0 request object"))
        rid, name, params = req.get("id"), req["method"], req.get("params", {})
        try:
            if name not in self.methods:
                raise RpcError(METHOD_NOT_FOUND, f"method {name!r} not found")
            if not isinstance(params, dict):
                raise RpcError(INVALID_PARAMS, "params must be an object")
            result = self.methods[name](params)
        except RpcError as exc:
            return _error(rid, exc)
        except Exception as exc:
            log.error("rpc %s failed: %s", name, traceback.format_exc())
            return _error(rid, RpcError(INTERNAL_ERROR, f"{type(exc).__name__}: {exc}"))
        return {"jsonrpc": "2.0", "id": rid, "result": result}

    def serve_forever(self) -> None:
        log.info("%s listening on %s", self.name, self.url)
        self.httpd.serve_forever(poll_interval=0.2)

    def start(self) -> RpcServer:
        self._thread = threading.Thread(target=self.serve_forever, name=self.name, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


def _error(rid, exc: RpcError) -> dict:
    return {"jsonrpc": "2.0", "id": rid, "error": exc.to_dict()}

"""Hub core: content-addressed blob store + model spec registry (PROTOCOL.md §4).

    python -m hub.core [--host 127.0.0.1] [--port 8800] [--root ~/flhub-runtime/hubcore]

PUT/GET/HEAD /blobs/sha256:<hex>   (PUT verifies the hash; idempotent)
POST/GET     /models/spec          ({"spec": [{"name","shape","dtype"}, ...]})
GET          /health
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import tempfile
from pathlib import Path

from adapters.common.protocol import is_ref
from adapters.common.server import Request, RpcServer

log = logging.getLogger("flhub.hubcore")
DEFAULT_ROOT = Path.home() / "flhub-runtime" / "hubcore"


class HubCore:
    def __init__(self, root: str | Path = DEFAULT_ROOT) -> None:
        self.root = Path(root).expanduser()
        self.blob_dir = self.root / "blobs"
        self.spec_path = self.root / "model_spec.json"
        self.blob_dir.mkdir(parents=True, exist_ok=True)

    def _blob_path(self, ref: str) -> Path:
        hexd = ref.split(":", 1)[1]
        return self.blob_dir / hexd[:2] / hexd

    def _write(self, path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as out:
            out.write(data)
        os.replace(out.name, path)

    def put_blob(self, req: Request):
        ref = req.path.rsplit("/", 1)[-1]
        if not is_ref(ref):
            return 400, {"error": f"bad ref {ref!r}"}
        if "sha256:" + hashlib.sha256(req.body).hexdigest() != ref:
            return 400, {"error": "body does not match the ref's sha256"}
        path = self._blob_path(ref)
        if path.exists():
            return 200, {"ref": ref, "created": False}
        self._write(path, req.body)
        return 201, {"ref": ref, "created": True}

    def get_blob(self, req: Request):
        ref = req.path.rsplit("/", 1)[-1]
        if not is_ref(ref):
            return 400, {"error": f"bad ref {ref!r}"}
        path = self._blob_path(ref)
        if not path.exists():
            return 404, {"error": f"blob {ref} not found"}
        return 200, path.read_bytes()

    def post_spec(self, req: Request):
        try:
            spec = req.json()["spec"]
            ok = isinstance(spec, list) and spec and all(
                isinstance(s, dict) and isinstance(s.get("name"), str) and isinstance(s.get("shape"), list)
                and isinstance(s.get("dtype"), str) for s in spec)
        except (ValueError, KeyError, TypeError):
            ok = False
        if not ok:
            return 400, {"error": "expected {'spec': [{'name','shape','dtype'}, ...]}"}
        self._write(self.spec_path, json.dumps({"spec": spec}, indent=1).encode())
        return 200, {"ok": True, "tensors": len(spec)}

    def get_spec(self, req: Request):
        if not self.spec_path.exists():
            return 404, {"error": "no model spec registered"}
        return 200, json.loads(self.spec_path.read_text())

    def health(self, req: Request):
        return 200, {"ok": True, "name": "hub-core", "spec": self.spec_path.exists()}

    def build_server(self, host: str = "127.0.0.1", port: int = 8800) -> RpcServer:
        srv = RpcServer(host, port, name="hub-core")
        srv.route("GET", "/health", self.health)
        srv.route("PUT", "/blobs/*", self.put_blob)
        srv.route("GET", "/blobs/*", self.get_blob)
        srv.route("POST", "/models/spec", self.post_spec)
        srv.route("GET", "/models/spec", self.get_spec)
        return srv


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m hub.core")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=int(os.environ.get("HUB_CORE_PORT", 8800)))
    ap.add_argument("--root", default=os.environ.get("FLHUB_HUBCORE_ROOT", str(DEFAULT_ROOT)))
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    HubCore(args.root).build_server(args.host, args.port).serve_forever()

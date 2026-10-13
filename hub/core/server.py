"""Hub core: content-addressed blob store + model spec registry (PROTOCOL.md §4).

    python -m hub.core [--host 127.0.0.1] [--port 8800] [--root ~/flhub-runtime/hubcore]

PUT/GET/HEAD /blobs/sha256:<hex>   (PUT verifies the hash; idempotent; bodies are streamed)
DELETE       /blobs/sha256:<hex>   (flhub/0.2: blob GC during and after runs)
POST         /admin/gc             {"older_than_s": N} deletes blobs not modified for N seconds
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
import time
from pathlib import Path

from adapters.common.protocol import is_ref
from adapters.common.server import CHUNK, Request, RpcServer

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
        """Streamed: hash while writing to a temp file, keep it only if the hash matches the ref."""
        ref = req.path.rsplit("/", 1)[-1]
        if not is_ref(ref):
            return 400, {"error": f"bad ref {ref!r}"}
        path = self._blob_path(ref)
        path.parent.mkdir(parents=True, exist_ok=True)
        h, left = hashlib.sha256(), req.length
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as out:
            try:
                while left > 0:
                    chunk = req.stream.read(min(CHUNK, left))
                    if not chunk:
                        break
                    h.update(chunk)
                    out.write(chunk)
                    left -= len(chunk)
            except BaseException:
                os.unlink(out.name)
                raise
        if left or "sha256:" + h.hexdigest() != ref:
            os.unlink(out.name)
            return 400, {"error": "body does not match the ref's sha256" if not left else "truncated body"}
        if path.exists():
            os.unlink(out.name)
            os.utime(path)  # a re-upload counts as fresh use for the GC
            return 200, {"ref": ref, "created": False}
        os.replace(out.name, path)
        return 201, {"ref": ref, "created": True}

    def get_blob(self, req: Request):
        ref = req.path.rsplit("/", 1)[-1]
        if not is_ref(ref):
            return 400, {"error": f"bad ref {ref!r}"}
        path = self._blob_path(ref)
        if not path.exists():
            return 404, {"error": f"blob {ref} not found"}
        return 200, path

    def delete_blob(self, req: Request):
        ref = req.path.rsplit("/", 1)[-1]
        if not is_ref(ref):
            return 400, {"error": f"bad ref {ref!r}"}
        try:
            self._blob_path(ref).unlink()
        except FileNotFoundError:
            return 404, {"error": f"blob {ref} not found"}
        return 200, {"ref": ref, "deleted": True}

    def gc(self, req: Request):
        body = req.json() or {}
        try:
            older = float(body.get("older_than_s", 0))
        except (TypeError, ValueError):
            return 400, {"error": "older_than_s must be a number"}
        cutoff, count, freed = time.time() - older, 0, 0
        for p in self.blob_dir.glob("*/*"):
            try:
                st = p.stat()
                if st.st_mtime <= cutoff:
                    p.unlink()
                    count, freed = count + 1, freed + st.st_size
            except FileNotFoundError:
                continue
        log.info("gc: deleted %d blobs (%.1f MB) older than %.0f s", count, freed / 2**20, older)
        return 200, {"deleted": count, "freed_bytes": freed}

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
        srv.route("PUT", "/blobs/*", self.put_blob, stream=True)
        srv.route("GET", "/blobs/*", self.get_blob)
        srv.route("DELETE", "/blobs/*", self.delete_blob)
        srv.route("POST", "/admin/gc", self.gc)
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

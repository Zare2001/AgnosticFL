"""Client for the hub core HTTP API (PROTOCOL.md §4): blobs and the model spec."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence

import numpy as np
from flhub_core.weights import from_safetensors, sha256_ref, to_safetensors

DEFAULT_HUB_URL = "http://127.0.0.1:8800"
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # localhost: never via a proxy


class HubError(RuntimeError):
    pass


def http_request(url: str, method: str = "GET", body: bytes | None = None,
                 content_type: str | None = None, timeout: float = 60) -> tuple[int, bytes]:
    """(status, body) for any response, including 4xx/5xx; raises HubError if unreachable."""
    req = urllib.request.Request(url, data=body, method=method)
    if content_type:
        req.add_header("Content-Type", content_type)
    try:
        with _opener.open(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except (urllib.error.URLError, OSError) as e:
        raise HubError(f"{method} {url}: {e}") from e


class HubClient:
    def __init__(self, base_url: str | None = None, timeout: float = 300) -> None:
        self.base_url = (base_url or os.environ.get("FLHUB_HUB_URL", DEFAULT_HUB_URL)).rstrip("/")
        self.timeout = timeout

    def _call(self, method: str, path: str, body: bytes | None = None, ctype: str | None = None):
        return http_request(self.base_url + path, method, body, ctype, self.timeout)

    def health(self) -> dict:
        status, body = self._call("GET", "/health")
        if status != 200:
            raise HubError(f"hub core unhealthy: HTTP {status}")
        return json.loads(body)

    def blob_url(self, ref: str) -> str:
        return f"{self.base_url}/blobs/{ref}"

    def put_blob(self, data: bytes) -> str:
        ref = sha256_ref(data)
        status, body = self._call("PUT", f"/blobs/{ref}", data, "application/octet-stream")
        if status not in (200, 201):
            raise HubError(f"PUT blob {ref}: HTTP {status} {body[:200]!r}")
        return ref

    def get_blob(self, ref: str) -> bytes:
        status, body = self._call("GET", f"/blobs/{ref}")
        if status != 200:
            raise HubError(f"GET blob {ref}: HTTP {status}")
        if sha256_ref(body) != ref:
            raise HubError(f"blob {ref} content does not match its hash")
        return body

    def has_blob(self, ref: str) -> bool:
        status, _ = self._call("HEAD", f"/blobs/{ref}")
        return status == 200

    def delete_blob(self, ref: str) -> bool:
        """flhub/0.2 blob GC; False if the blob was not there."""
        status, body = self._call("DELETE", f"/blobs/{ref}")
        if status not in (200, 404):
            raise HubError(f"DELETE blob {ref}: HTTP {status} {body[:200]!r}")
        return status == 200

    def gc(self, older_than_s: float = 0) -> dict:
        """Delete every blob not written or re-uploaded for `older_than_s` seconds."""
        status, body = self._call("POST", "/admin/gc", json.dumps({"older_than_s": older_than_s}).encode(),
                                  "application/json")
        if status != 200:
            raise HubError(f"POST /admin/gc: HTTP {status} {body[:200]!r}")
        return json.loads(body)

    def put_weights(self, weights: Mapping[str, np.ndarray]) -> str:
        return self.put_blob(to_safetensors(weights))

    def get_weights(self, ref: str, names: Sequence[str] | None = None) -> dict[str, np.ndarray]:
        return from_safetensors(self.get_blob(ref), names)

    def put_spec(self, spec: Sequence[Mapping]) -> None:
        status, body = self._call("POST", "/models/spec", json.dumps({"spec": list(spec)}).encode(),
                                  "application/json")
        if status != 200:
            raise HubError(f"POST spec: HTTP {status} {body[:200]!r}")

    def get_spec(self) -> list[dict] | None:
        status, body = self._call("GET", "/models/spec")
        if status == 404:
            return None
        if status != 200:
            raise HubError(f"GET spec: HTTP {status}")
        return json.loads(body)["spec"]

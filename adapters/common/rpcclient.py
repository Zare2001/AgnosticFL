"""JSON-RPC 2.0 client for flhub/0.1 and 0.2 adapters (used by the orchestrator and the conformance tests)."""

from __future__ import annotations

import itertools
import json

from .hubclient import HubError, http_request
from .protocol import INTERNAL_ERROR, PROTOCOL, PROTOCOL_V1, UNSUPPORTED_PROTOCOL, RpcError


class AdapterClient:
    def __init__(self, url: str, timeout: float = 30, protocol: str = PROTOCOL) -> None:
        base = url.rstrip("/")
        self.url = base if base.endswith("/rpc") else base + "/rpc"
        self.timeout = timeout
        self.protocol = protocol
        self._ids = itertools.count(1)

    def negotiate(self) -> dict:
        """discover with flhub/0.2, falling back to 0.1 for an older adapter; sets self.protocol."""
        self.protocol = PROTOCOL
        try:
            return self.discover()
        except RpcError as exc:
            if exc.code != UNSUPPORTED_PROTOCOL:
                raise
        self.protocol = PROTOCOL_V1
        return self.discover()

    def call(self, method: str, **params):
        """Call `method`; adds `protocol` unless given. Raises RpcError on an error response."""
        params.setdefault("protocol", self.protocol)
        req = {"jsonrpc": "2.0", "id": next(self._ids), "method": method, "params": params}
        try:
            status, body = http_request(self.url, "POST", json.dumps(req).encode(), "application/json", self.timeout)
        except HubError as exc:
            raise RpcError(INTERNAL_ERROR, f"adapter unreachable: {exc}") from exc
        if status != 200:
            raise RpcError(INTERNAL_ERROR, f"HTTP {status} from {self.url}: {body[:200]!r}")
        resp = json.loads(body)
        if "error" in resp:
            e = resp["error"]
            raise RpcError(e.get("code", INTERNAL_ERROR), e.get("message", ""), e.get("data"))
        return resp["result"]

    def discover(self) -> dict:
        return self.call("discover")

    def dispatch(self, round: int, model: dict, train_config: dict, sites: list[str], deadline_s: float = 1800,
                 data_variant: str | None = None) -> str:
        extra = {"data_variant": data_variant} if data_variant is not None else {}
        return self.call("dispatch", round=round, model=model, train_config=train_config, sites=sites,
                         deadline_s=deadline_s, **extra)["task_id"]

    def get_task(self, task_id: str) -> dict:
        return self.call("tasks/get", task_id=task_id)

    def collect(self, task_id: str) -> list[dict]:
        return self.call("collect", task_id=task_id)["envelopes"]

    def collect_partial(self, task_id: str) -> dict:
        """flhub/0.2: {"envelopes": [done sites], "state", "sites": {site: state}} in any task state."""
        return self.call("collect", task_id=task_id, partial=True)

    def cancel(self, task_id: str, sites: list[str] | None = None) -> dict:
        """Whole task, or (flhub/0.2) only `sites`; the other sites go on."""
        if sites is None:
            return self.call("cancel", task_id=task_id)
        return self.call("cancel", task_id=task_id, sites=list(sites))

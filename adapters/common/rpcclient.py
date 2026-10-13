"""JSON-RPC 2.0 client for flhub/0.1 adapters (used by the orchestrator and the conformance tests)."""

from __future__ import annotations

import itertools
import json

from .hubclient import HubError, http_request
from .protocol import INTERNAL_ERROR, PROTOCOL, RpcError


class AdapterClient:
    def __init__(self, url: str, timeout: float = 30) -> None:
        base = url.rstrip("/")
        self.url = base if base.endswith("/rpc") else base + "/rpc"
        self.timeout = timeout
        self._ids = itertools.count(1)

    def call(self, method: str, **params):
        """Call `method`; adds `protocol` unless given. Raises RpcError on an error response."""
        params.setdefault("protocol", PROTOCOL)
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

    def dispatch(self, round: int, model: dict, train_config: dict, sites: list[str], deadline_s: float = 1800) -> str:
        return self.call("dispatch", round=round, model=model, train_config=train_config, sites=sites,
                         deadline_s=deadline_s)["task_id"]

    def get_task(self, task_id: str) -> dict:
        return self.call("tasks/get", task_id=task_id)

    def collect(self, task_id: str) -> list[dict]:
        return self.call("collect", task_id=task_id)["envelopes"]

    def cancel(self, task_id: str) -> dict:
        return self.call("cancel", task_id=task_id)

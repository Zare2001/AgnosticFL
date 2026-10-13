"""flhub/0.2 constants and small helpers shared by adapters and the orchestrator.

flhub/0.2 (ADR-008) only adds optional fields to flhub/0.1, so a 0.2 adapter also serves
0.1 callers: it accepts both versions and echoes the request's version in its envelopes.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime, timezone

PROTOCOL = "flhub/0.2"
PROTOCOL_V1 = "flhub/0.1"
SUPPORTED_PROTOCOLS = (PROTOCOL_V1, PROTOCOL)

# JSON-RPC 2.0 standard codes
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
# flhub/0.1 codes (PROTOCOL.md §2)
UNSUPPORTED_PROTOCOL = -32010
UNKNOWN_SITE = -32011
TASK_NOT_FOUND = -32012
TASK_NOT_COMPLETED = -32013
FRAMEWORK_ERROR = -32014
ADAPTER_BUSY = -32015

TASK_STATES = ("queued", "running", "completed", "failed", "cancelled")
TERMINAL_STATES = frozenset({"completed", "failed", "cancelled"})
SITE_STATES = ("online", "offline", "unknown")
# flhub/0.2 per-site task progress (tasks/get "sites")
SITE_TASK_STATES = ("queued", "running", "done", "failed", "cancelled")
SITE_ACTIVE_STATES = frozenset({"queued", "running"})
TRAIN_CONFIG_KEYS = ("local_epochs", "batch_size", "lr", "momentum", "seed")


class RpcError(Exception):
    """Raise inside an RPC method to return a JSON-RPC error object."""

    def __init__(self, code: int, message: str, data=None) -> None:
        super().__init__(message)
        self.code, self.message, self.data = code, message, data

    def to_dict(self) -> dict:
        err = {"code": self.code, "message": self.message}
        if self.data is not None:
            err["data"] = self.data
        return err


def canonical_json(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def train_config_hash(train_config: Mapping) -> str:
    """`sha256:` of the canonical JSON of the dispatch train_config (without `round`)."""
    return "sha256:" + hashlib.sha256(canonical_json(dict(train_config))).hexdigest()


def client_config(train_config: Mapping, rnd: int) -> dict:
    """What clients receive: the dispatch train_config plus the round number."""
    return {**dict(train_config), "round": int(rnd)}


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def check_protocol(params: Mapping) -> str:
    """The request's protocol version; -32010 unless it is one this adapter speaks."""
    got = params.get("protocol") if isinstance(params, Mapping) else None
    if got not in SUPPORTED_PROTOCOLS:
        raise RpcError(UNSUPPORTED_PROTOCOL,
                       f"unsupported protocol {got!r}; this adapter speaks {', '.join(SUPPORTED_PROTOCOLS)}")
    return got


def is_ref(value) -> bool:
    return (isinstance(value, str) and value.startswith("sha256:") and len(value) == 71
            and all(c in "0123456789abcdef" for c in value[7:]))

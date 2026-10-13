"""Task book of the Flower adapter: dispatched rounds, relay hand-off, envelopes.

Pure state (no I/O) so it can be unit-tested without Flower. All methods are
thread-safe; ``next_job`` blocks (long-poll) until a round is queued.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

PROTOCOL = "flhub/0.1"
FRAMEWORK = "flower"
TERMINAL = ("completed", "failed", "cancelled")

RELAY_STALE_S = 30.0  # relay counts as alive if heard from within this window
RELAY_LOST_S = 90.0  # a running task fails if its relay is silent this long
DEADLINE_GRACE_S = 30.0


class RpcError(Exception):
    """JSON-RPC error with a flhub/0.1 error code."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def train_config_hash(train_config: dict[str, Any]) -> str:
    canonical = json.dumps(train_config, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass
class Task:
    task_id: str
    round: int
    model: dict[str, str]
    train_config: dict[str, Any]
    sites: list[str]
    deadline_s: float
    blob_base_url: str
    state: str = "queued"
    created: float = field(default_factory=time.monotonic)
    started: float | None = None
    relay_id: str | None = None
    last_contact: float = field(default_factory=time.monotonic)
    results: dict[str, dict[str, Any]] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    error: str | None = None

    def job(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "round": self.round,
            "model": self.model,
            "train_config": self.train_config,
            "sites": self.sites,
            "deadline_s": self.deadline_s,
            "blob_base_url": self.blob_base_url,
        }


class TaskBook:
    """Tasks plus the view of the single active relay."""

    def __init__(self, sites: list[str]) -> None:
        self.sites = list(sites)
        self._cv = threading.Condition()
        self._tasks: dict[str, Task] = {}
        self._queue: deque[str] = deque()
        self.relay_id: str | None = None
        self.relay_seen = 0.0
        self.relay_nodes: list[dict[str, Any]] = []
        self.stopping = False

    # ------------------------------------------------------------------ relay view
    def relay_alive(self) -> bool:
        with self._cv:
            return self._relay_alive()

    def _relay_alive(self) -> bool:
        return self.relay_id is not None and time.monotonic() - self.relay_seen < RELAY_STALE_S

    def site_status(self) -> list[dict[str, str]]:
        with self._cv:
            if not self._relay_alive():
                return [{"site_id": s, "status": "unknown"} for s in self.sites]
            online = {n.get("site_id") for n in self.relay_nodes}
            return [{"site_id": s, "status": "online" if s in online else "offline"}
                    for s in self.sites]

    # ------------------------------------------------------------------ JSON-RPC side
    def dispatch(self, params: dict[str, Any], blob_base_url: str) -> dict[str, str]:
        rnd = int(params["round"])
        sites = list(params.get("sites") or self.sites)
        if len(set(sites)) != len(sites):
            raise RpcError(-32602, f"duplicate sites in dispatch: {sites}")
        unknown = [s for s in sites if s not in self.sites]
        if unknown:
            raise RpcError(-32011, f"unknown site(s) for flower adapter: {unknown}")
        model = params["model"]
        task = Task(
            task_id=f"flower-r{rnd}-{uuid.uuid4().hex[:8]}",
            round=rnd,
            model={"ref": str(model["ref"]), "url": str(model["url"])},
            train_config=dict(params["train_config"]),
            sites=sites,
            deadline_s=float(params.get("deadline_s", 1800)),
            blob_base_url=blob_base_url,
        )
        with self._cv:
            self._expire()
            busy = [t.task_id for t in self._tasks.values()
                    if t.state not in TERMINAL and set(t.sites) & set(sites)]
            if busy:
                raise RpcError(-32015, f"adapter busy: task(s) {busy} still running for these sites")
            self._tasks[task.task_id] = task
            self._queue.append(task.task_id)
            self._cv.notify_all()
        return {"task_id": task.task_id}

    def get(self, task_id: str) -> dict[str, Any]:
        with self._cv:
            self._expire()
            t = self._task(task_id)
            return {
                "task_id": t.task_id,
                "state": t.state,
                "progress": {"done": len(t.results), "total": len(t.sites)},
                "error": t.error,
            }

    def collect(self, task_id: str) -> dict[str, Any]:
        with self._cv:
            self._expire()
            t = self._task(task_id)
            if t.state != "completed":
                raise RpcError(-32013, f"task {task_id} is {t.state}, not completed")
            cfg_hash = train_config_hash(t.train_config)
            envelopes = []
            for site in t.sites:
                r = t.results[site]
                envelopes.append({
                    "protocol": PROTOCOL,
                    "round": t.round,
                    "site_id": site,
                    "framework": FRAMEWORK,
                    "base_model": t.model["ref"],
                    "weights": r["weights"],
                    "num_examples": int(r["num_examples"]),
                    "metrics": r.get("metrics", {}),
                    "train_config_hash": cfg_hash,
                    "client_code_version": r.get("client_code_version", ""),
                    "created_at": r["created_at"],
                })
            return {"task_id": task_id, "envelopes": envelopes}

    def cancel(self, task_id: str) -> dict[str, str]:
        with self._cv:
            t = self._task(task_id)
            if t.state not in TERMINAL:
                t.state = "cancelled"
                if task_id in self._queue:
                    self._queue.remove(task_id)
            return {"task_id": task_id, "state": t.state}

    # ------------------------------------------------------------------ relay side
    def next_job(self, relay_id: str, nodes: list[dict[str, Any]], wait_s: float) -> dict[str, Any]:
        """Long-poll from the relay: returns a train job, idle or shutdown."""
        end = time.monotonic() + max(0.0, min(wait_s, 30.0))
        with self._cv:
            if self._relay_alive() and self.relay_id != relay_id:
                return {"type": "shutdown", "reason": f"relay {self.relay_id} is active"}
            self.relay_id = relay_id
            self.relay_seen = time.monotonic()
            self.relay_nodes = list(nodes)
            while True:
                if self.stopping:
                    return {"type": "shutdown", "reason": "adapter stopping"}
                while self._queue:
                    t = self._tasks[self._queue.popleft()]
                    if t.state != "queued":
                        continue
                    t.state = "running"
                    t.started = t.last_contact = time.monotonic()
                    t.relay_id = relay_id
                    return {"type": "train", "job": t.job()}
                remaining = end - time.monotonic()
                if remaining <= 0:
                    return {"type": "idle"}
                self._cv.wait(remaining)

    def relay_result(self, payload: dict[str, Any]) -> dict[str, bool]:
        """Per-site results/errors and heartbeats from the relay for one task."""
        with self._cv:
            relay_id = str(payload.get("relay_id"))
            if relay_id == self.relay_id:
                self.relay_seen = time.monotonic()
            t = self._tasks.get(str(payload.get("task_id")))
            if t is None or t.state != "running" or t.relay_id != relay_id:
                return {"cancel": True}
            t.last_contact = time.monotonic()
            for r in payload.get("results", []):
                if r["site_id"] in t.sites:
                    t.results[r["site_id"]] = {**r, "created_at": utc_now()}
            for e in payload.get("errors", []):
                if e["site_id"] in t.sites:
                    t.errors[e["site_id"]] = str(e["error"])
            accounted = set(t.results) | set(t.errors)
            if payload.get("done") or accounted >= set(t.sites):
                self._finish(t)
            return {"cancel": False}

    # ------------------------------------------------------------------ internals
    def _task(self, task_id: str) -> Task:
        t = self._tasks.get(task_id)
        if t is None:
            raise RpcError(-32012, f"task {task_id} not found")
        return t

    def _finish(self, t: Task) -> None:
        missing = [s for s in t.sites if s not in t.results and s not in t.errors]
        for s in missing:
            t.errors[s] = "relay finished without a result"
        if t.errors:
            t.state = "failed"
            t.error = "; ".join(f"{s}: {msg}" for s, msg in sorted(t.errors.items()))
        else:
            t.state = "completed"

    def _expire(self) -> None:
        now = time.monotonic()
        for t in self._tasks.values():
            if t.state == "running" and now - t.last_contact > RELAY_LOST_S:
                t.state, t.error = "failed", f"relay {t.relay_id} lost (silent {RELAY_LOST_S:.0f} s)"
            elif t.state in ("queued", "running") and now - t.created > t.deadline_s + DEADLINE_GRACE_S:
                t.state, t.error = "failed", f"deadline_s={t.deadline_s:.0f} exceeded"

    def stop(self) -> None:
        with self._cv:
            self.stopping = True
            self._cv.notify_all()

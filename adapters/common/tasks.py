"""Task registry for adapters: one task = one dispatched round on some sites."""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field

from .protocol import ADAPTER_BUSY, TASK_NOT_FOUND, TERMINAL_STATES, RpcError, client_config


@dataclass
class Task:
    task_id: str
    round: int
    sites: list[str]
    model_ref: str
    train_config: dict
    deadline_s: float
    state: str = "queued"
    done: int = 0
    error: str | None = None
    envelopes: list[dict] = field(default_factory=list)
    started: float = field(default_factory=time.monotonic)
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)

    @property
    def cancelled(self) -> bool:
        return self.cancel_event.is_set()

    @property
    def total(self) -> int:
        return len(self.sites)

    @property
    def client_config(self) -> dict:
        return client_config(self.train_config, self.round)

    @property
    def terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    def seconds_left(self) -> float:
        return self.deadline_s - (time.monotonic() - self.started)

    def deadline_exceeded(self) -> bool:
        return self.seconds_left() <= 0

    def status(self) -> dict:
        return {"task_id": self.task_id, "state": self.state,
                "progress": {"done": self.done, "total": self.total}, "error": self.error}


class TaskRegistry:
    def __init__(self, keep: int = 500) -> None:
        self._tasks: dict[str, Task] = {}
        self._lock = threading.RLock()
        self._keep = keep

    def create(self, prefix: str, round: int, sites: list[str], model_ref: str, train_config: dict,
               deadline_s: float) -> Task:
        with self._lock:
            busy = [t for t in self._tasks.values() if not t.terminal and set(t.sites) & set(sites)]
            if busy:
                raise RpcError(ADAPTER_BUSY, f"task {busy[0].task_id} is still {busy[0].state} for these sites")
            task = Task(f"{prefix}-r{round}-{uuid.uuid4().hex[:8]}", round, list(sites), model_ref,
                        dict(train_config), float(deadline_s))
            self._tasks[task.task_id] = task
            while len(self._tasks) > self._keep:  # forget the oldest finished tasks
                old = next((k for k, t in self._tasks.items() if t.terminal), None)
                if old is None:
                    break
                del self._tasks[old]
            return task

    def get(self, task_id) -> Task:
        with self._lock:
            if task_id not in self._tasks:
                raise RpcError(TASK_NOT_FOUND, f"task {task_id!r} not found")
            return self._tasks[task_id]

    def start(self, task: Task) -> bool:
        with self._lock:
            if task.state != "queued":
                return False
            task.state = "running"
            return True

    def finish(self, task: Task, state: str, error: str | None = None, envelopes: list[dict] | None = None) -> bool:
        """Move a task to a terminal state once; returns False if it was already terminal."""
        with self._lock:
            if task.terminal:
                return False
            if envelopes is not None:
                task.envelopes = envelopes
                task.done = task.total
            task.error = error
            task.state = state
            return True

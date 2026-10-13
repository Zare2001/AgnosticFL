"""Task registry for adapters: one task = one dispatched round on some sites.

flhub/0.2 tracks every site of a task separately (queued, running, done, failed, cancelled).
A site's envelope is kept as soon as the site is done, so partial collect works while the
task still runs and after it failed or was cancelled. A task ends (completed, failed or
cancelled) once no site is queued or running anymore, or when it is cancelled as a whole,
times out or its framework work fails. All transitions run under the registry lock.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field

from .protocol import (
    ADAPTER_BUSY,
    PROTOCOL,
    SITE_ACTIVE_STATES,
    TASK_NOT_FOUND,
    TERMINAL_STATES,
    RpcError,
    client_config,
)


@dataclass
class Task:
    task_id: str
    round: int
    sites: list[str]
    model_ref: str
    train_config: dict
    deadline_s: float
    protocol: str = PROTOCOL  # version of the dispatch request; echoed in the envelopes
    data_variant: str | None = None  # flhub/0.2 dispatch hint for adapters that select data themselves
    state: str = "queued"
    done: int = 0  # progress reported by the framework (0.1 style); sites done count as well
    error: str | None = None
    site_state: dict[str, str] = field(default_factory=dict)
    site_error: dict[str, str] = field(default_factory=dict)
    site_envelopes: dict[str, dict] = field(default_factory=dict)
    started: float = field(default_factory=time.monotonic)
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)

    def __post_init__(self) -> None:
        if not self.site_state:
            self.site_state = {s: "queued" for s in self.sites}

    @property
    def cancelled(self) -> bool:
        """True once the whole task was cancelled or timed out: framework work should stop."""
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

    @property
    def envelopes(self) -> list[dict]:
        """Envelopes of the sites that are done, in dispatch order."""
        return [self.site_envelopes[s] for s in self.sites if s in self.site_envelopes]

    def active_sites(self) -> list[str]:
        """Sites still queued or running, in dispatch order."""
        return [s for s in self.sites if self.site_state.get(s) in SITE_ACTIVE_STATES]

    def site_cancelled(self, site: str) -> bool:
        return self.site_state.get(site) == "cancelled"

    def seconds_left(self) -> float:
        return self.deadline_s - (time.monotonic() - self.started)

    def deadline_exceeded(self) -> bool:
        return self.seconds_left() <= 0

    def status(self) -> dict:
        done_sites = sum(1 for s in self.sites if self.site_state.get(s) == "done")
        out = {"task_id": self.task_id, "state": self.state,
               "progress": {"done": max(min(self.done, self.total), done_sites), "total": self.total},
               "error": self.error, "sites": dict(self.site_state)}
        if self.site_error:
            out["site_errors"] = dict(self.site_error)
        return out


class TaskRegistry:
    def __init__(self, keep: int = 500) -> None:
        self._tasks: dict[str, Task] = {}
        self._lock = threading.RLock()
        self._keep = keep

    @property
    def lock(self) -> threading.RLock:
        return self._lock

    def create(self, prefix: str, round: int, sites: list[str], model_ref: str, train_config: dict,
               deadline_s: float, protocol: str = PROTOCOL, data_variant: str | None = None) -> Task:
        """New task; -32015 if one of the sites is still queued or running in another task."""
        with self._lock:
            for t in self._tasks.values():
                busy = [s for s in sites if not t.terminal and t.site_state.get(s) in SITE_ACTIVE_STATES]
                if busy:
                    raise RpcError(ADAPTER_BUSY, f"task {t.task_id} is still {t.state} for sites {busy}")
            task = Task(f"{prefix}-r{round}-{uuid.uuid4().hex[:8]}", round, list(sites), model_ref,
                        dict(train_config), float(deadline_s), protocol=protocol, data_variant=data_variant)
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
            for s in task.active_sites():
                task.site_state[s] = "running"
            return True

    # ---- per-site transitions (flhub/0.2) -----------------------------------------------
    def site_done(self, task: Task, site: str, envelope: dict) -> bool:
        """Keep the site's envelope; False (result dropped) if the site is no longer active."""
        with self._lock:
            if task.site_state.get(site) not in SITE_ACTIVE_STATES:
                return False
            task.site_state[site] = "done"
            task.site_envelopes[site] = envelope
            self._settle(task)
            return True

    def site_failed(self, task: Task, site: str, error: str) -> bool:
        with self._lock:
            if task.site_state.get(site) not in SITE_ACTIVE_STATES:
                return False
            task.site_state[site] = "failed"
            task.site_error[site] = error
            self._settle(task)
            return True

    def cancel_sites(self, task: Task, sites: list[str]) -> list[str]:
        """Cancel the listed sites that are still queued or running; returns them."""
        with self._lock:
            hit = [s for s in sites if task.site_state.get(s) in SITE_ACTIVE_STATES]
            for s in hit:
                task.site_state[s] = "cancelled"
            self._settle(task)
            return hit

    def _settle(self, task: Task) -> None:
        """End the task once no site is queued or running (caller holds the lock)."""
        if task.terminal or task.active_sites():
            return
        states = set(task.site_state.values())
        if states == {"done"}:
            self._end(task, "completed", None)
        elif "failed" in states:
            self._end(task, "failed", "; ".join(f"{s}: {e}" for s, e in task.site_error.items()))
        else:
            self._end(task, "cancelled", "cancelled by orchestrator: " + ", ".join(
                s for s in task.sites if task.site_state[s] == "cancelled"))

    def _end(self, task: Task, state: str, error: str | None) -> None:
        task.state = state
        task.error = error
        if state != "completed":
            task.cancel_event.set()

    # ---- whole-task transitions -----------------------------------------------------------
    def finish(self, task: Task, state: str, error: str | None = None, envelopes: list[dict] | None = None) -> bool:
        """Move a task to a terminal state once; returns False if it was already terminal.

        failed / cancelled: the sites still active become failed / cancelled; done sites keep
        their envelopes for partial collect. completed: `envelopes` (if given) complete the
        active sites; an active site without one failed ("no result"), and the final state
        follows from all site states.
        """
        with self._lock:
            if task.terminal:
                return False
            if envelopes is not None:
                for env in envelopes:
                    s = env["site_id"]
                    if task.site_state.get(s) in SITE_ACTIVE_STATES:
                        task.site_state[s] = "done"
                        task.site_envelopes[s] = env
                task.done = task.total
            if state == "completed":
                for s in task.active_sites():
                    task.site_state[s] = "failed"
                    task.site_error[s] = "no result from the framework"
                self._settle(task)
                return True
            for s in task.active_sites():
                if state == "cancelled":
                    task.site_state[s] = "cancelled"
                else:
                    task.site_state[s] = "failed"
                    task.site_error[s] = error or state
            self._end(task, state, error)
            return True

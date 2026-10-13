"""Run directory: checkpoints after every round, resume, retention (stage-2 PLAN.md §4.3, §7).

    <runs_dir>/<name>-<UTC stamp>/
        config.json          resolved run config + environment
        metrics.jsonl        one line per step (round 0 = initial model)
        events.jsonl         retries, failures, dropped sites, resumes (UTC times)
        rounds/rNNNN.safetensors        global weights after round N
        rounds/rNNNN.state.safetensors  aggregator state after round N
        rounds/rNNNN.json               round record; written last, so it marks a complete round
        summary.json
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from flhub_core.weights import from_safetensors, sha256_ref, to_safetensors

from hub.aggregators import state_bytes, state_from_bytes


def utc_now(ms: bool = False) -> str:
    fmt = "%Y-%m-%dT%H:%M:%S.%f" if ms else "%Y-%m-%dT%H:%M:%S"
    s = datetime.now(timezone.utc).strftime(fmt)
    return (s[:-3] if ms else s) + "Z"


def write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as out:
        out.write(data)
        out.flush()
        os.fsync(out.fileno())
    os.replace(out.name, path)


class RunDir:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        self.rounds = self.path / "rounds"

    @classmethod
    def create(cls, runs_dir: str | Path, name: str) -> RunDir:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        root = Path(runs_dir).expanduser()
        path = root / f"{name}-{stamp}"
        (path / "rounds").mkdir(parents=True, exist_ok=False)
        latest = root / f"{name}-latest"
        if latest.is_symlink() or latest.exists():
            latest.unlink()
        latest.symlink_to(path.name)
        return cls(path)

    # ---- files ------------------------------------------------------------------------------
    def weights_path(self, rnd: int) -> Path:
        return self.rounds / f"r{rnd:04d}.safetensors"

    def state_path(self, rnd: int) -> Path:
        return self.rounds / f"r{rnd:04d}.state.safetensors"

    def record_path(self, rnd: int) -> Path:
        return self.rounds / f"r{rnd:04d}.json"

    def write_json(self, name: str, obj) -> None:
        write_atomic(self.path / name, (json.dumps(obj, indent=1) + "\n").encode())

    def read_json(self, name: str):
        return json.loads((self.path / name).read_text())

    # ---- rounds -----------------------------------------------------------------------------
    def save_round(self, rnd: int, weights, state, record: dict) -> dict:
        """Weights, state, then the record (commit marker). Returns the record with the refs."""
        wbytes, sbytes = to_safetensors(weights), state_bytes(state)
        write_atomic(self.weights_path(rnd), wbytes)
        write_atomic(self.state_path(rnd), sbytes)
        record = {**record, "round": rnd, "global_ref": sha256_ref(wbytes), "agg_state_ref": sha256_ref(sbytes)}
        write_atomic(self.record_path(rnd), (json.dumps(record, indent=1) + "\n").encode())
        return record

    def complete_rounds(self) -> list[int]:
        out = []
        for p in sorted(self.rounds.glob("r[0-9][0-9][0-9][0-9].json")):
            rnd = int(p.stem[1:])
            if self.weights_path(rnd).exists() and self.state_path(rnd).exists():
                out.append(rnd)
        return out

    def recorded_rounds(self) -> list[int]:
        return [int(p.stem[1:]) for p in sorted(self.rounds.glob("r[0-9][0-9][0-9][0-9].json"))]

    def last_complete_round(self) -> int | None:
        rounds = self.complete_rounds()
        return rounds[-1] if rounds else None

    def record(self, rnd: int) -> dict:
        return json.loads(self.record_path(rnd).read_text())

    def load_weights(self, rnd: int, names=None):
        return from_safetensors(self.weights_path(rnd).read_bytes(), names)

    def load_state(self, rnd: int) -> dict:
        return state_from_bytes(self.state_path(rnd).read_bytes())

    # ---- logs -------------------------------------------------------------------------------
    def append_metrics(self, step: int, metrics: dict) -> None:
        with open(self.path / "metrics.jsonl", "a") as f:
            f.write(json.dumps({"step": step, **metrics}) + "\n")

    def metrics(self) -> list[dict]:
        path = self.path / "metrics.jsonl"
        out = []
        if path.exists():
            for line in path.read_text().splitlines():
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue  # a line cut by a crash
        return out

    def truncate_metrics(self, max_step: int) -> None:
        """Keep one line per step <= max_step (resume after a crash)."""
        seen, keep = set(), []
        for m in self.metrics():
            if m["step"] <= max_step and m["step"] not in seen:
                seen.add(m["step"])
                keep.append(m)
        write_atomic(self.path / "metrics.jsonl", "".join(json.dumps(m) + "\n" for m in keep).encode())

    def event(self, kind: str, **fields) -> None:
        with open(self.path / "events.jsonl", "a") as f:
            f.write(json.dumps({"utc": utc_now(ms=True), "event": kind, **fields}) + "\n")

    def events(self) -> list[dict]:
        path = self.path / "events.jsonl"
        return [json.loads(x) for x in path.read_text().splitlines() if x.strip()] if path.exists() else []

    # ---- retention (applied by finalize_run.py after the gate passed) -------------------------
    def apply_retention(self, keep: set[int]) -> int:
        """Delete weight and state checkpoints of rounds not in `keep` (records stay); bytes freed."""
        freed = 0
        for rnd in self.recorded_rounds():
            if rnd in keep:
                continue
            for p in (self.weights_path(rnd), self.state_path(rnd)):
                if p.exists():
                    freed += p.stat().st_size
                    p.unlink()
        return freed


def retention_rounds(last: int, every: int = 10, replay: bool = False) -> set[int]:
    if replay:
        return {r for r in (0, 1, 2, 3, last - 1, last) if 0 <= r <= last}
    return {0, last} | {r for r in range(0, last + 1, max(1, every))}

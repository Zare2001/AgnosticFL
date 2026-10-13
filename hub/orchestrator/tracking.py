"""Run tracking: metrics.jsonl in the run dir, plus Weights & Biases (offline by default)."""

from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger("flhub.tracking")


class Tracker:
    def __init__(self, run_dir: Path, mode: str, name: str, config: dict, project: str | None = None,
                 entity: str | None = None, group: str | None = None, tags: list[str] | None = None,
                 job_type: str = "train") -> None:
        self.path = run_dir / "metrics.jsonl"
        self.run = None
        if mode != "disabled":
            import wandb

            self.run = wandb.init(project=project or "AgnosticFL", entity=entity or None, name=name, group=group,
                                  tags=list(tags or []), config=config, dir=str(run_dir), mode=mode,
                                  job_type=job_type, settings=wandb.Settings(console="off", silent=True))
            log.info("W&B run %s (%s) %s", self.run.id, mode, self.url or "")

    @property
    def url(self) -> str | None:
        return getattr(self.run, "url", None) if self.run else None

    @property
    def run_id(self) -> str | None:
        return self.run.id if self.run else None

    def log(self, step: int, metrics: dict) -> None:
        with open(self.path, "a") as f:
            f.write(json.dumps({"step": step, **metrics}) + "\n")
        if self.run:
            self.run.log(metrics, step=step)

    def update_config(self, values: dict) -> None:
        if self.run:
            self.run.config.update(values, allow_val_change=True)

    def summary(self, values: dict) -> None:
        if self.run:
            self.run.summary.update(values)

    def finish(self, ok: bool) -> None:
        if self.run:
            self.run.finish(exit_code=0 if ok else 1)
            self.run = None

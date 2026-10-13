"""Run configuration (configs/*.yaml)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from adapters.common.protocol import TRAIN_CONFIG_KEYS

DEFAULT_TRAIN = {"local_epochs": 1, "batch_size": 64, "lr": 0.05, "momentum": 0.0, "seed": 2026}


@dataclass
class AdapterSpec:
    name: str
    url: str
    sites: list[str]


@dataclass
class RunConfig:
    name: str
    rounds: int = 30
    seed: int = 2026  # initial model seed
    train_config: dict = field(default_factory=lambda: dict(DEFAULT_TRAIN))
    adapters: list[AdapterSpec] = field(default_factory=list)
    hub_url: str = "http://127.0.0.1:8800"
    deadline_s: float = 1800.0
    online_wait_s: float = 600.0
    poll_interval_s: float = 1.0
    round_retries: int = 0  # re-run a failed round (same model, same config => same result)
    runs_dir: str = "~/flhub-runtime/runs"
    wandb_project: str | None = None
    wandb_entity: str | None = None
    group: str | None = None
    tags: list[str] = field(default_factory=list)
    source: str | None = None  # config file path

    @property
    def sites(self) -> list[str]:
        return [s for a in self.adapters for s in a.sites]

    def to_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items() if k != "adapters"}
        d["adapters"] = [a.__dict__ for a in self.adapters]
        return d


def load_config(path: str | Path, **overrides) -> RunConfig:
    raw = yaml.safe_load(Path(path).read_text()) or {}
    raw.update({k: v for k, v in overrides.items() if v is not None})
    adapters = [AdapterSpec(a["name"], a["url"], list(a["sites"])) for a in raw.pop("adapters", [])]
    train = {**DEFAULT_TRAIN, **(raw.pop("train_config", None) or {})}
    missing = [k for k in TRAIN_CONFIG_KEYS if k not in train]
    if missing:
        raise ValueError(f"train_config lacks {missing}")
    wandb = raw.pop("wandb", None) or {}
    cfg = RunConfig(adapters=adapters, train_config=train, source=str(path),
                    wandb_project=wandb.get("project"), wandb_entity=wandb.get("entity"),
                    group=raw.pop("group", wandb.get("group")),
                    tags=list(raw.pop("tags", None) or wandb.get("tags") or []), **raw)
    sites = cfg.sites
    if len(set(sites)) != len(sites):
        raise ValueError(f"a site is listed under two adapters: {sites}")
    if cfg.rounds < 1:
        raise ValueError("rounds must be >= 1")
    return cfg

"""Run configuration: schema 2 (configs/stage2/*.yaml) and the stage-1 schema (configs/*.yaml).

Schema 2 (stage-2 PLAN.md §4.3):

    schema: 2
    name: s2-e1-dir0.3-fedavg
    experiment: E1                     # W&B group E<n>
    mode: real                         # real | sim | reference | native (finalize gate)
    dataset: organamnist
    partition: dir0.3                  # data_variant organamnist-dir0.3 (omit: the stage-1 data)
    model: cnn-small
    aggregator: {name: fedavg, params: {}}
    train_config: {local_epochs: 1, batch_size: 64, lr: 0.05, momentum: 0.0}   # + weight_decay
    seed: 2026                         # model init and train_config seed
    rounds: 50
    sites: [nvf-h1, nvf-h2, v6-h3, v6-h4, flw-h5, flw-h6]
    participation: {fraction: 1.0, seed: 2026, min_sites: null}
    deadline_s: 1800
    round_retries: 1
    eval: {every: 1, site_shaped: true, split: test, threads: 1}
    retention: {keep_every: 10, blobs: rolling, checkpoints: all}   # checkpoints: latest = payload runs
    wandb: {group: E1, tags: [...]}
    netem: none

Sites are mapped to adapters by configs/stage2/topology.yaml (or `topology:`). The dispatched
train_config is flat: the four keys above + seed + model + data_variant (+ weight_decay if set,
+ prox_mu for fedprox). Stage-1 configs keep their exact train_config (bit-exact regression).
"""

from __future__ import annotations

import copy
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml
from flhub_core.data import dataset_info, parse_variant
from flhub_core.model import check_model, is_payload

from adapters.common.protocol import TRAIN_CONFIG_KEYS
from hub.aggregators import make_aggregator

REPO = Path(__file__).resolve().parents[2]
DEFAULT_TOPOLOGY = REPO / "configs" / "stage2" / "topology.yaml"
DEFAULT_TRAIN = {"local_epochs": 1, "batch_size": 64, "lr": 0.05, "momentum": 0.0, "seed": 2026}
MODES = ("real", "sim", "reference", "native")


@dataclass
class AdapterSpec:
    name: str
    url: str
    sites: list[str]


@dataclass
class RunConfig:
    name: str
    schema: int = 2
    experiment: str | None = None
    mode: str = "real"
    dataset: str = "pneumoniamnist"
    partition: str | None = None
    model: str = "cnn-small"
    aggregator: dict = field(default_factory=lambda: {"name": "fedavg", "params": {}})
    train_config: dict = field(default_factory=lambda: dict(DEFAULT_TRAIN))
    rounds: int = 30
    seed: int = 2026
    sites: list[str] = field(default_factory=list)
    adapters: list[AdapterSpec] = field(default_factory=list)
    participation: dict = field(default_factory=lambda: {"fraction": 1.0, "seed": 2026, "min_sites": None})
    deadline_s: float = 1800.0
    round_retries: int = 0
    online_wait_s: float = 600.0
    poll_interval_s: float = 0.25
    eval: dict = field(default_factory=lambda: {"every": 1, "site_shaped": True, "split": "test", "threads": 1})
    retention: dict = field(default_factory=lambda: {"keep_every": 10, "blobs": "keep"})
    wandb: dict = field(default_factory=dict)
    hub_url: str = "http://127.0.0.1:8800"
    runs_dir: str = "~/flhub-runtime/runs"
    netem: str = "none"
    notes: str = ""
    strict_code_version: bool = False
    source: str | None = None

    @property
    def data_variant(self) -> str | None:
        return f"{self.dataset}-{self.partition}" if self.partition else None

    @property
    def num_classes(self) -> int:
        return dataset_info(self.dataset).num_classes

    @property
    def min_sites(self) -> int | None:
        return self.participation.get("min_sites")

    def aggregator_obj(self):
        return make_aggregator(self.aggregator["name"], self.aggregator.get("params") or {})

    def to_dict(self) -> dict:
        d = asdict(self)
        d["data_variant"] = self.data_variant
        return d

    def with_seed(self, seed: int) -> RunConfig:
        """Same config with another training seed (model init + data order); partitions unchanged."""
        cfg = copy.deepcopy(self)
        cfg.seed = int(seed)
        cfg.train_config["seed"] = int(seed)
        return cfg


def load_topology(path: str | Path | None = None) -> tuple[str, list[AdapterSpec]]:
    raw = yaml.safe_load(Path(path or DEFAULT_TOPOLOGY).read_text()) or {}
    adapters = [AdapterSpec(name, a["url"], list(a["sites"])) for name, a in (raw.get("adapters") or {}).items()]
    return raw.get("hub_url", "http://127.0.0.1:8800"), adapters


def _schema1(raw: dict, path: str) -> RunConfig:
    """Stage-1 config: adapters listed inline, stage-1 data, fedavg, train_config dispatched as is."""
    adapters = [AdapterSpec(a["name"], a["url"], list(a["sites"])) for a in raw.pop("adapters", [])]
    train = {**DEFAULT_TRAIN, **(raw.pop("train_config", None) or {})}
    wandb = raw.pop("wandb", None) or {}
    group, tags = raw.pop("group", wandb.get("group")), list(raw.pop("tags", None) or wandb.get("tags") or [])
    keep = {k: raw[k] for k in ("rounds", "seed", "hub_url", "deadline_s", "online_wait_s", "poll_interval_s",
                                "round_retries", "runs_dir") if k in raw}
    return RunConfig(name=raw.get("name", Path(path).stem), schema=1, adapters=adapters, train_config=train,
                     sites=[s for a in adapters for s in a.sites], wandb={**wandb, "group": group, "tags": tags},
                     eval={"every": 1, "site_shaped": False, "split": "test", "threads": 1}, source=str(path),
                     **keep)


def _schema2(raw: dict, path: str) -> RunConfig:
    hub_url, topo = load_topology(raw.pop("topology", None))
    sites = list(raw.pop("sites"))
    known = {s: a for a in topo for s in a.sites}
    unknown = [s for s in sites if s not in known]
    if unknown:
        raise ValueError(f"sites {unknown} are not in the topology")
    adapters = [AdapterSpec(a.name, a.url, [s for s in sites if s in a.sites]) for a in topo]
    adapters = [a for a in adapters if a.sites]
    defaults = RunConfig(name="")
    cfg = RunConfig(
        name=raw.pop("name"), schema=2, sites=sites, adapters=adapters, hub_url=raw.pop("hub_url", hub_url),
        participation={**defaults.participation, **(raw.pop("participation", None) or {})},
        eval={**defaults.eval, **(raw.pop("eval", None) or {})},
        retention={**defaults.retention, **(raw.pop("retention", None) or {})},
        aggregator={"name": "fedavg", "params": {}, **(raw.pop("aggregator", None) or {})},
        train_config=dict(raw.pop("train_config", None) or {}), source=str(path), **raw)
    cfg.train_config = build_train_config(cfg)
    return cfg


def build_train_config(cfg: RunConfig) -> dict:
    """The flat train_config sent to the sites (schema 2)."""
    base = {**DEFAULT_TRAIN, **cfg.train_config}
    base["seed"] = int(cfg.train_config.get("seed", cfg.seed))
    tc = {k: base[k] for k in TRAIN_CONFIG_KEYS}
    tc["model"] = check_model(cfg.model)
    if cfg.data_variant:
        parse_variant(cfg.data_variant)
        tc["data_variant"] = cfg.data_variant
    if float(base.get("weight_decay", 0.0)):
        tc["weight_decay"] = float(base["weight_decay"])
    tc.update(cfg.aggregator_obj().client_config())
    bad = {k: v for k, v in tc.items() if v is None or isinstance(v, (dict, list))}
    if bad:
        raise ValueError(f"train_config must be flat scalars: {bad}")
    return tc


def validate(cfg: RunConfig) -> RunConfig:
    if cfg.rounds < 1:
        raise ValueError("rounds must be >= 1")
    if len(set(cfg.sites)) != len(cfg.sites) or not cfg.sites:
        raise ValueError(f"sites must be a non-empty list without duplicates: {cfg.sites}")
    if cfg.mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    missing = [k for k in TRAIN_CONFIG_KEYS if k not in cfg.train_config]
    if missing:
        raise ValueError(f"train_config lacks {missing}")
    fraction = float(cfg.participation.get("fraction", 1.0))
    if not 0 < fraction <= 1:
        raise ValueError("participation.fraction must be in (0, 1]")
    ms = cfg.min_sites
    if ms is not None and not 1 <= int(ms) <= len(cfg.sites):
        raise ValueError(f"participation.min_sites must be in 1..{len(cfg.sites)}")
    if is_payload(cfg.model) and cfg.eval.get("every"):
        cfg.eval["every"] = 0  # nothing to evaluate for an echo payload
    cfg.aggregator_obj()  # checks name and params
    return cfg


def deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        out[k] = deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load_raw(path: str | Path) -> dict:
    """YAML with `extends: <file relative to this one>` resolved (deep merge, this file wins)."""
    path = Path(path)
    raw = yaml.safe_load(path.read_text()) or {}
    parent = raw.pop("extends", None)
    return deep_merge(load_raw(path.parent / parent), raw) if parent else raw


def load_config(path: str | Path, **overrides) -> RunConfig:
    raw = load_raw(path)
    raw.update({k: v for k, v in overrides.items() if v is not None})
    cfg = _schema2(raw, str(path)) if int(raw.pop("schema", 1)) >= 2 else _schema1(raw, str(path))
    return validate(cfg)


def config_from_dict(d: dict) -> RunConfig:
    """Rebuild a RunConfig from config.json["run_config"] (resume, replay)."""
    d = {k: v for k, v in d.items() if k != "data_variant"}
    d["adapters"] = [AdapterSpec(**a) for a in d.get("adapters", [])]
    return RunConfig(**d)


def dumps(cfg: RunConfig) -> str:
    return json.dumps(cfg.to_dict(), indent=1, sort_keys=True)

"""Cross-machine determinism check.

    python -m flhub_core.selfcheck [--site nvf-h1] [--data FILE]

Trains one local round (round 1, default config) from the seeded initial model on a site
partition and prints the sha256 of the result. Every VM and the vantage6 algorithm image
must print the same `result` line; otherwise FedAvg across frameworks cannot be bit-exact.
"""

from __future__ import annotations

import argparse
import platform
import time
from pathlib import Path

import torch

from . import code_version
from .data import SITES, load_site, materialize, site_path
from .model import init_weights
from .train import TrainConfig, train
from .weights import sha256_ref, to_safetensors


def _cpu_model() -> str:
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or "unknown"


def main(argv: list[str] | None = None) -> str:
    ap = argparse.ArgumentParser(prog="python -m flhub_core.selfcheck")
    ap.add_argument("--site", default="nvf-h1", choices=sorted(SITES))
    ap.add_argument("--data", default=None, help="site CSV (default: $FLHUB_DATA_DIR/<site>.csv, materialized if missing)")
    args = ap.parse_args(argv)
    path = Path(args.data) if args.data else site_path(args.site)
    if not path.exists():
        path = materialize(args.site)
    data = load_site(path)
    w0 = init_weights(2026)
    t0 = time.time()
    w1, n, metrics = train(w0, data, TrainConfig(round=1))
    result = sha256_ref(to_safetensors(w1))
    print(f"flhub_core  {code_version()}")
    print(f"python      {platform.python_version()}  torch {torch.__version__}  "
          f"cpu_capability {torch.backends.cpu.get_cpu_capability()}")
    print(f"cpu         {_cpu_model()} ({platform.machine()})")
    print(f"data        {path} n={n} fingerprint={data.fingerprint[:16]}")
    print(f"init        {sha256_ref(to_safetensors(w0))}")
    print(f"result      {result}  train_loss={metrics['train_loss']:.8f}  ({time.time() - t0:.2f}s)")
    return result


if __name__ == "__main__":
    main()

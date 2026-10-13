"""Cross-machine determinism check.

    python -m flhub_core.selfcheck                                 # v2: every model of the zoo
    python -m flhub_core.selfcheck --v1 [--site nvf-h1] [--data FILE]   # stage-1 check

v2 needs no files and no network: per case it trains one local round (round 1, seed 2026) from
the seeded initial model on a fixed synthetic 11-class dataset and prints the sha256 of the result,
plus one `digest` line over all results. Every VM, the vantage6 image, the NVFlare job and the
Flower FAB runtime must print the same lines; otherwise FedAvg across frameworks is not bit-exact.
v1 trains the stage-1 model on a site partition (stage-1 hash 7c80a5a3... for nvf-h1).
"""

from __future__ import annotations

import argparse
import hashlib
import platform
import time
from pathlib import Path

import numpy as np
import torch

from . import code_version
from .data import SITES, from_arrays, load_site, materialize, site_path
from .model import init_weights, num_parameters
from .train import TrainConfig, train
from .weights import sha256_ref, to_safetensors

V2_CLASSES = 11
V2_VARIANT = "organamnist-iid"  # gives train() the 11-class dataset; the synthetic data is used as given
# (case, model, extra train_config, synthetic examples)
V2_CASES = [
    ("cnn-small", "cnn-small", {}, 256),
    ("cnn-small+prox+wd+mom", "cnn-small", {"prox_mu": 0.01, "weight_decay": 1e-4, "momentum": 0.9}, 256),
    ("cnn-medium", "cnn-medium", {}, 256),
    ("resnet18-gn", "resnet18-gn", {}, 64),
    ("payload-8", "payload-8", {}, 256),
]


def _cpu_model() -> str:
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or "unknown"


def synthetic_data(n: int, seed: int = 2026, num_classes: int = V2_CLASSES):
    rng = np.random.RandomState(seed)
    pixels = rng.randint(0, 256, size=(n, 28 * 28)).astype(np.uint8)
    labels = rng.randint(0, num_classes, size=n)
    return from_arrays(pixels, labels)


def run_v2() -> dict[str, str]:
    results = {}
    for case, model, extra, n in V2_CASES:
        data = synthetic_data(n)
        w0 = init_weights(2026, model, V2_CLASSES)
        cfg = TrainConfig(round=1, model=model, data_variant=V2_VARIANT, **extra)
        t0 = time.time()
        w1, _, metrics = train(w0, data, cfg)
        results[case] = sha256_ref(to_safetensors(w1))
        print(f"{case:24s} params={num_parameters(model, V2_CLASSES):>10,}  result {results[case]}  "
              f"train_loss={metrics['train_loss']:.8f}  ({time.time() - t0:.1f}s)", flush=True)
    digest = hashlib.sha256("".join(f"{k}={v};" for k, v in results.items()).encode()).hexdigest()
    print(f"{'digest':24s} sha256:{digest}")
    return results


def run_v1(site: str, data_path: str | None) -> str:
    path = Path(data_path) if data_path else site_path(site)
    if not path.exists():
        path = materialize(site)
    data = load_site(path)
    w0 = init_weights(2026)
    t0 = time.time()
    w1, n, metrics = train(w0, data, TrainConfig(round=1))
    result = sha256_ref(to_safetensors(w1))
    print(f"data        {path} n={n} fingerprint={data.fingerprint[:16]}")
    print(f"init        {sha256_ref(to_safetensors(w0))}")
    print(f"result      {result}  train_loss={metrics['train_loss']:.8f}  ({time.time() - t0:.2f}s)")
    return result


def main(argv: list[str] | None = None):
    ap = argparse.ArgumentParser(prog="python -m flhub_core.selfcheck")
    ap.add_argument("--v1", action="store_true", help="stage-1 check on a site partition instead of v2")
    ap.add_argument("--site", default="nvf-h1", choices=sorted(SITES))
    ap.add_argument("--data", default=None, help="v1: site CSV (default: $FLHUB_DATA_DIR/<site>.csv)")
    args = ap.parse_args(argv)
    print(f"flhub_core  {code_version()}")
    print(f"python      {platform.python_version()}  torch {torch.__version__}  numpy {np.__version__}  "
          f"cpu_capability {torch.backends.cpu.get_cpu_capability()}")
    print(f"cpu         {_cpu_model()} ({platform.machine()})", flush=True)
    return run_v1(args.site, args.data) if args.v1 else run_v2()


if __name__ == "__main__":
    main()

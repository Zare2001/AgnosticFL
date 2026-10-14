"""Run the example algorithm without a vantage6 node and check it against the client contract.

vantage6's own MockAlgorithmClient (from vantage6-algorithm-tools) calls the algorithm as a node does:
it passes the database as a DataFrame to @data and the task input as keyword arguments. This script
gives @metadata what a node gives it (a run token and the run's file paths), trains one round on a
benchmark site, and checks the result as the adapter and the conformance suite do: the update must
equal flhub_core.train on the same data, bit for bit.

    python examples/vantage6/client/run_local.py [--site v6-h3] [--data-dir DIR]

Needs torch, numpy, safetensors, pandas and vantage6-algorithm-tools at the versions of
deploy/versions.env (commands in examples/vantage6/README.md). The run downloads the public
PneumoniaMNIST file (4 MB) into DIR; by default DIR is a temporary directory.
"""

from __future__ import annotations

import argparse
import base64
import os
import signal
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path[:0] = [str(HERE), str(REPO / "flhub_core" / "src")]  # the example package and flhub_core of this checkout

import jwt  # noqa: E402  PyJWT, installed with vantage6-algorithm-tools
import pandas as pd  # noqa: E402
from vantage6.algorithm.tools.mock_client import MockAlgorithmClient  # noqa: E402

import flhub_core  # noqa: E402
from flhub_core import data as fdata  # noqa: E402
from flhub_core import train as ftrain  # noqa: E402
from flhub_core import weights as fweights  # noqa: E402
from flhub_core.model import init_weights  # noqa: E402

# what the adapter sends: the hub's train_config plus the round number
TRAIN_CONFIG = {"local_epochs": 1, "batch_size": 64, "lr": 0.05, "momentum": 0.0, "seed": 2026, "round": 1}
ORG_ID = 2  # any organisation id; the `sites` input maps it to the site id


def node_environment(run_dir: Path, org_id: int) -> None:
    """What a vantage6 node gives its algorithm container for @metadata: a run token whose `sub` holds
    the run's ids (@metadata reads it without checking the signature) and the run's file paths."""
    sub = {"task_id": 1, "node_id": 1, "collaboration_id": 1, "organization_id": org_id}
    (run_dir / "token").write_text(jwt.encode({"sub": sub}, "local-run-only-not-a-node-token-0000", algorithm="HS256"))
    os.environ.update(TOKEN_FILE=str(run_dir / "token"), TEMPORARY_FOLDER=str(run_dir),
                      INPUT_FILE=str(run_dir / "input"), OUTPUT_FILE=str(run_dir / "output"))


def main(argv: list[str] | None = None) -> int:
    # SIGTERM (for example from `timeout`) ends Python without its clean-up; exit normally instead, so that
    # the temporary directory with the dataset, the site CSV and the run token is removed
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    ap = argparse.ArgumentParser(prog="run_local.py", description="Run the vantage6 example algorithm locally.")
    ap.add_argument("--site", default="v6-h3", help="benchmark site whose partition is used (flhub_core.data.SITES)")
    ap.add_argument("--data-dir", help="where the dataset and the site CSV are kept (default: a temporary directory)")
    args = ap.parse_args(argv)
    with tempfile.TemporaryDirectory() as tmp:
        run_dir = Path(tmp)
        data_dir = Path(args.data_dir).expanduser() if args.data_dir else run_dir
        csv = fdata.materialize(args.site, data_dir, cache_dir=data_dir / "raw")  # the site's partition, sha256-checked
        df = pd.read_csv(csv)  # what @data(1) passes for a CSV database on a node
        node_environment(run_dir, ORG_ID)

        weights = init_weights(2026)  # the global model of round 0
        raw = fweights.to_safetensors(weights)
        client = MockAlgorithmClient(datasets=[[{"database": df, "db_type": "csv"}]], module="v6_example",
                                     organization_ids=[ORG_ID])
        task = client.task.create(input_={"method": "train", "kwargs": {
            "weights_b64": base64.b64encode(raw).decode("ascii"), "train_config": TRAIN_CONFIG,
            "sites": {str(ORG_ID): args.site}}}, organizations=[ORG_ID])
        result = client.result.from_task(task["id"])[0]

        # the checks of the adapter and of the conformance suite: the right site and base model, and an
        # update equal to flhub_core.train on the same data, here read from the CSV file
        expected, n, metrics = ftrain.train(weights, fdata.load_site(csv), TRAIN_CONFIG)
        diff = fweights.max_abs_diff(fweights.from_b64(result["weights_b64"]), expected)
    checks = {
        "site_id": result["site_id"] == args.site,
        "base_model": result["base_model"] == fweights.sha256_ref(raw),
        "num_examples": result["num_examples"] == n,
        "data_fp": result["metrics"]["data_fp"] == metrics["data_fp"],
        "client_code_version": result["client_code_version"] == flhub_core.code_version(),
        "weights equal flhub_core.train": diff == 0.0,
    }
    print(f"site {args.site}: num_examples {result['num_examples']}, train_loss {result['metrics']['train_loss']:.6f}, "
          f"max abs diff {diff:.1e}, client_code_version {result['client_code_version']}")
    for name, ok in checks.items():
        print(f"  {'OK  ' if ok else 'FAIL'} {name}")
    print("PASS" if all(checks.values()) else "FAIL")
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    sys.exit(main())

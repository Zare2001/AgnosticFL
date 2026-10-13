"""E7 native baseline: FedAvg the vantage6 way, without the hub (EXPERIMENTS.md E7).

A vantage6 algorithm has a central function, run by the node of the organisation that receives
the task, which creates subtasks for the data organisations and aggregates their results. Here:
- `train` is the unchanged partial function of the hub image (v6_flhub.train), so the hospitals run
  exactly the code they run for the hub;
- `fedavg` is the central function: per round one `train` subtask for all data organisations,
  results via the node proxy, FedAvg weighted by num_examples (float64 accumulation in site order,
  one cast to float32), next round from the new global model.
The image is the hub image plus this package (PKG_NAME=v6_native); subtasks inherit image and
databases from the central task.
"""

from __future__ import annotations

import base64
import hashlib
import time

import numpy as np
from flhub_core import weights as fweights
from v6_flhub import train  # noqa: F401  (the partial function, re-exported for the subtasks)
from vantage6.algorithm.tools.decorators import algorithm_client
from vantage6.algorithm.tools.util import info


def _ref(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _fedavg(results: list[tuple[str, dict, int]]) -> dict:
    ordered = sorted(results, key=lambda r: r[0])
    total = np.float64(sum(n for _, _, n in ordered))
    out = {}
    for name in ordered[0][1]:
        acc = np.zeros(ordered[0][1][name].shape, dtype=np.float64)
        for _, w, n in ordered:
            acc += np.float64(n) * w[name].astype(np.float64)
        out[name] = (acc / total).astype(np.float32)
    return out


@algorithm_client
def fedavg(client, init_b64: str, train_config: dict, rounds: int, sites: dict, keep_weights: bool = True) -> dict:
    """Run `rounds` FedAvg rounds over the organisations in `sites` ({org id: site id})."""
    orgs = [int(o) for o in sites]
    raw = base64.b64decode(init_b64)
    global_w = fweights.from_safetensors(raw)
    out = {"rounds": [{"round": 0, "global_ref": _ref(raw), **({"global_b64": init_b64} if keep_weights else {})}]}
    for rnd in range(1, int(rounds) + 1):
        t0 = time.time()
        task = client.task.create(
            input_={"method": "train", "kwargs": {"weights_b64": base64.b64encode(raw).decode("ascii"),
                                                  "train_config": {**train_config, "round": rnd}, "sites": sites}},
            organizations=orgs, name=f"native-r{rnd}", description=f"E7 native FedAvg round {rnd}")
        results = client.wait_for_results(task_id=task["id"], interval=1)
        t_results = time.time()
        updates, rec = [], {}
        for res in results:
            w_raw = base64.b64decode(res["weights_b64"])
            updates.append((res["site_id"], fweights.from_safetensors(w_raw), int(res["num_examples"])))
            rec[res["site_id"]] = {"num_examples": int(res["num_examples"]), "metrics": res["metrics"],
                                   "weights_ref": _ref(w_raw), "client_code_version": res["client_code_version"],
                                   **({"weights_b64": res["weights_b64"]} if keep_weights else {})}
        global_w = _fedavg(updates)
        raw = fweights.to_safetensors(global_w)
        t_done = time.time()
        info(f"native round {rnd}: {len(updates)} sites, {t_done - t0:.1f} s, global {_ref(raw)[:19]}")
        out["rounds"].append({"round": rnd, "vantage6_task_id": task["id"], "t_start": t0, "t_results": t_results,
                              "t_done": t_done, "duration_s": t_done - t0, "aggregation_s": t_done - t_results,
                              "global_ref": _ref(raw), "sites": rec,
                              **({"global_b64": base64.b64encode(raw).decode("ascii")} if keep_weights else {})})
    if not keep_weights:  # payload runs: hashes per round, weights of the final round only
        out["final_global_b64"] = base64.b64encode(raw).decode("ascii")
    return out

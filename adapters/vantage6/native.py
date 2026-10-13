"""E7 native baseline driver: FedAvg the vantage6 way on the hub's vantage6 hospitals, no hub.

The researcher side (this module, as user hub-adapter) creates ONE vantage6 task for organisation
hub-operator: the central function `v6_native.fedavg` (deploy/central/vantage6/native/v6_native),
which the hub-operator node next to the server runs. It creates one `train` subtask per round for
the data organisations (unchanged v6_flhub.train in the same image), aggregates FedAvg weighted by
num_examples and returns every round's global model and client results (payload runs: hashes and
the final model only). This driver writes them in the hub's run-dir layout.

On central (deploy/central/vantage6/native/run.sh):
    python -m adapters.vantage6.native run --config configs/stage2/e7/s2-e7-vantage6-hub-cnn.yaml --out RUN_DIR --image IMG
    python -m adapters.vantage6.native compare RUN_DIR HUB_RUN_DIR      (E7 analysis)
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from adapters.vantage6.service import db_label

log = logging.getLogger("flhub.native.vantage6")
FAILED = {"failed", "start failed", "non-existing Docker image", "crashed", "killed by user", "not allowed",
          "unknown error"}


def utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_run_config(path: str, name: str | None = None) -> dict:
    """The native run's settings from the E7 hub run config, through central's loader (so extends,
    partition, model and seed resolve as for the hub run); name s2-e7-vantage6-hub-X -> -native-X."""
    from hub.orchestrator.config import build_train_config, load_config

    cfg = load_config(path)
    return {"name": name or cfg.name.replace("-hub-", "-native-"), "hub_name": cfg.name, "rounds": cfg.rounds,
            "seed": cfg.seed, "sites": list(cfg.sites), "model": cfg.model, "num_classes": cfg.num_classes,
            "data_variant": cfg.data_variant, "train_config": build_train_config(cfg),
            "deadline_s": cfg.deadline_s, "source": str(path)}


def _client(server: str, port: int):
    from vantage6.client import UserClient

    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    client = UserClient(server, port, "/api", log_level="warn")
    root.handlers[:] = handlers
    root.setLevel(level)
    client.log = logging.getLogger("vantage6.client")
    client.log.setLevel(logging.WARNING)
    client.authenticate(os.environ.get("V6_ADAPTER_USER", "hub-adapter"), os.environ["V6_ADAPTER_PASSWORD"])
    client.setup_encryption(None)
    return client


def run(config: str, out: str, image: str, server: str, port: int, rounds: int | None = None) -> Path:
    import flhub_core
    from flhub_core.model import init_weights
    from flhub_core.weights import sha256_ref, to_safetensors

    rcfg = load_run_config(config)
    if rounds:  # dry runs only; final runs use the config's rounds
        rcfg.update(rounds=int(rounds), dry_run=True)
    run_dir = Path(out).expanduser().resolve()
    (run_dir / "rounds").mkdir(parents=True, exist_ok=False)
    init = to_safetensors(init_weights(rcfg["seed"], rcfg["model"], rcfg["num_classes"]))  # = the hub's round 0
    keep = not str(rcfg["model"]).startswith("payload-")
    rcfg.update(mode="native", framework="vantage6", image=image, init_ref=sha256_ref(init), keep_weights=keep,
                flhub_core_version=flhub_core.code_version(), started_at=utc())
    (run_dir / "native_config.json").write_text(json.dumps(rcfg, indent=1))

    client = _client(server, port)
    collab = next(c for c in client.collaboration.list(per_page=100)["data"] if c["name"] == "flhub")
    orgs = {o["name"]: o["id"] for o in client.organization.list(per_page=100)["data"]}
    sites = {str(orgs[s]): s for s in rcfg["sites"]}
    t0 = time.time()
    task = client.task.create(
        collaboration=collab["id"], organizations=[orgs["hub-operator"]], name=rcfg["name"], image=image,
        description=f"E7 native FedAvg ({rcfg['hub_name']})",
        input_={"method": "fedavg", "kwargs": {"init_b64": base64.b64encode(init).decode("ascii"),
                                               "train_config": rcfg["train_config"], "rounds": rcfg["rounds"],
                                               "sites": sites, "keep_weights": keep}},
        databases=[{"label": db_label(rcfg["data_variant"])}])
    log.info("vantage6 task %s (central function fedavg on hub-operator), %s rounds", task["id"], rcfg["rounds"])
    deadline = t0 + rcfg["rounds"] * float(rcfg["deadline_s"] or 1800)
    while True:
        status = client.request(f"task/{task['id']}/status").get("status")
        if status == "completed" or status in FAILED or time.time() > deadline:
            break
        time.sleep(2)
    job = {"vantage6_task_id": task["id"], "status": status, "seconds": round(time.time() - t0, 1),
           "finished_at": utc()}
    if status == "completed":
        items = client.result.from_task(task["id"])["data"]
        _write_rounds(run_dir, json.loads(items[0]["result"]), keep)
    else:
        runs = client.request("run", params={"task_id": task["id"]}).get("data", [])
        job["log"] = [(r.get("log") or "")[-2000:] for r in runs]
    (run_dir / "native_job.json").write_text(json.dumps(job, indent=1))
    finalize(run_dir)
    return run_dir


def _write_rounds(run_dir: Path, res: dict, keep: bool) -> None:
    rounds = run_dir / "rounds"
    for rec in res["rounds"]:
        rnd = rec["round"]
        if keep or rnd == len(res["rounds"]) - 1:
            b64 = rec.get("global_b64") or (res.get("final_global_b64") if rnd == len(res["rounds"]) - 1 else None)
            if b64:
                (rounds / f"r{rnd:04d}.safetensors").write_bytes(base64.b64decode(b64))
        for site, s in rec.get("sites", {}).items():
            if s.get("weights_b64"):
                (rounds / f"r{rnd:04d}-{site}.safetensors").write_bytes(base64.b64decode(s.pop("weights_b64")))
        rec.pop("global_b64", None)
        (rounds / f"r{rnd:04d}.json").write_text(json.dumps(rec, indent=1))


def finalize(run_dir: str | Path) -> dict:
    """metrics.jsonl + summary.json from the recorded rounds (hub field names where they exist)."""
    run_dir = Path(run_dir)
    rcfg = json.loads((run_dir / "native_config.json").read_text())
    job = json.loads((run_dir / "native_job.json").read_text()) if (run_dir / "native_job.json").exists() else {}
    rows = []
    for rnd in range(0, rcfg["rounds"] + 1):
        p = run_dir / "rounds" / f"r{rnd:04d}.json"
        if not p.exists():
            break
        rec = json.loads(p.read_text())
        row = {"step": rnd, "global_ref": rec["global_ref"]}
        if rnd:
            row.update({"round/duration_s": round(rec["duration_s"], 3), "round/aggregation_s": round(rec["aggregation_s"], 3),
                        "round/num_sites": len(rec["sites"]),
                        "round/num_examples": sum(int(s["num_examples"]) for s in rec["sites"].values())})
            for site, s in sorted(rec["sites"].items()):
                row[f"site/{site}/num_examples"] = s["num_examples"]
                row[f"site/{site}/t_result_s"] = round(rec["t_results"] - rec["t_start"], 3)  # all sites at once
                for k, v in (s["metrics"] or {}).items():
                    row[f"site/{site}/{k}"] = v
                if "train_time_s" in (s["metrics"] or {}):
                    row[f"site/{site}/overhead_s"] = round(rec["t_results"] - rec["t_start"] - s["metrics"]["train_time_s"], 3)
        rows.append(row)
    (run_dir / "metrics.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    done = len(rows) - 1
    summary = {"status": "finished" if done == rcfg["rounds"] and job.get("status") == "completed" else "failed",
               "mode": "native", "framework": "vantage6", "rounds": done, "job": job,
               "final/global_ref": rows[-1]["global_ref"] if rows else None, "run_dir": str(run_dir)}
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps({k: v for k, v in summary.items() if k != "job"}))
    return summary


def compare(native_dir: str, hub_dir: str) -> dict:
    """Per round: max abs difference native vs hub global weights (both evolve separately; equal
    hashes -> 0.0), and the one-step aggregation difference (hub FedAvg of the native round's own
    client results vs the native global). Writes native_vs_hub.json."""
    import numpy as np
    from flhub_core.weights import from_safetensors

    from hub.orchestrator.fedavg import fedavg

    nat, hub = Path(native_dir) / "rounds", Path(hub_dir) / "rounds"
    load = lambda p: from_safetensors(p.read_bytes())  # noqa: E731
    maxdiff = lambda a, b: max(float(np.max(np.abs(a[k] - b[k]))) for k in a)  # noqa: E731
    rows, rnd = [], 0
    while (nat / f"r{rnd:04d}.json").exists():
        rec = json.loads((nat / f"r{rnd:04d}.json").read_text())
        row = {"round": rnd, "native_ref": rec["global_ref"]}
        hub_rec = hub / f"r{rnd:04d}.json"
        if hub_rec.exists():
            row["hub_ref"] = json.loads(hub_rec.read_text()).get("global_ref")
        if row.get("hub_ref") == rec["global_ref"]:
            row["global_max_abs_diff"] = 0.0
        elif (nat / f"r{rnd:04d}.safetensors").exists() and (hub / f"r{rnd:04d}.safetensors").exists():
            row["global_max_abs_diff"] = maxdiff(load(nat / f"r{rnd:04d}.safetensors"),
                                                 load(hub / f"r{rnd:04d}.safetensors"))
        clients = [(s, nat / f"r{rnd:04d}-{s}.safetensors", v["num_examples"]) for s, v in rec.get("sites", {}).items()]
        if clients and all(p.exists() for _, p, _ in clients) and (nat / f"r{rnd:04d}.safetensors").exists():
            agg = fedavg([(s, load(p), int(n)) for s, p, n in clients])
            row["one_step_aggregation_diff"] = maxdiff(load(nat / f"r{rnd:04d}.safetensors"), agg)
        rows.append(row)
        rnd += 1
    diffs = [r["global_max_abs_diff"] for r in rows if "global_max_abs_diff" in r]
    out = {"native": str(native_dir), "hub": str(hub_dir), "rounds": rows,
           "native_vs_hub_max_diff": max(diffs) if diffs else None}
    (Path(native_dir) / "native_vs_hub.json").write_text(json.dumps(out, indent=1))
    print(json.dumps({k: v for k, v in out.items() if k != "rounds"}))
    return out


def main(argv=None) -> None:
    env = os.environ.get
    ap = argparse.ArgumentParser(prog="python -m adapters.vantage6.native")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--config", required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--image", required=True, help="native image (<hub image>-native)")
    r.add_argument("--server-url", default=env("V6_SERVER_URL", "http://127.0.0.1"))
    r.add_argument("--server-port", type=int, default=int(env("V6_SERVER_PORT", "7601")))
    r.add_argument("--rounds", type=int, default=None, help="dry runs only")
    c = sub.add_parser("compare")
    c.add_argument("native_dir")
    c.add_argument("hub_dir")
    f = sub.add_parser("finalize")
    f.add_argument("run_dir")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    if args.cmd == "run":
        run(args.config, args.out, args.image, args.server_url, args.server_port, args.rounds)
    elif args.cmd == "compare":
        compare(args.native_dir, args.hub_dir)
    else:
        finalize(args.run_dir)


if __name__ == "__main__":
    main()

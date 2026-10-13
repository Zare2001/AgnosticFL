"""E7 native baseline: NVFlare's built-in FedAvg on the hub's NVFlare hospitals, without the hub.

NativeFedAvg is nvflare.app_common.workflows.fedavg.FedAvg (client sampling, InTime aggregation
weighted by FLModel.meta NUM_STEPS_CURRENT_ROUND = num_examples, round loop) with additions that
leave the aggregation untouched:
- the initial model is the hub's round-0 global model (a safetensors file);
- every task carries the hub train_config (+ "round") in FLModel.meta["train_config"], as the relay
  job does (FedAvg itself replaces the model meta after each round);
- every client result and every round's global model are recorded in the run dir.
The clients run the relay job's unchanged custom code: clients/nvflare/client.py + flhub_core.

On central (deploy/central/nvflare/native/run.sh):
    python -m adapters.nvflare.native prepare --config RUN.yaml --out RUN_DIR [--name NAME]
    python -m adapters.nvflare.native submit RUN_DIR --admin-kit KIT [--timeout S]
    python -m adapters.nvflare.native finalize RUN_DIR
    python -m adapters.nvflare.native compare RUN_DIR HUB_RUN_DIR   (E7 analysis)
"""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from nvflare.app_common.abstract.fl_model import FLModel, ParamsType
from nvflare.app_common.workflows.fedavg import FedAvg

META_TRAIN_CONFIG = "train_config"
NATIVE_JOB = "flhub-native"


def utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class NativeFedAvg(FedAvg):
    def __init__(self, *args, init_path: str = "", train_config: dict = None, run_dir: str = "",
                 keep_client_weights: bool = True, **kwargs):
        """FedAvg plus recording; init_path = hub round-0 weights (safetensors), run_dir = output.
        Attributes keep the parameter names and builtin types: the Job API serializes them."""
        kwargs.update(persistor_id="", save_filename=None)  # no persistor, no FedAvg model file
        super().__init__(*args, **kwargs)
        self.init_path, self.run_dir = init_path, str(run_dir)
        self.train_config = dict(train_config or {})
        self.keep_client_weights = keep_client_weights
        self._t_send, self._replies = 0.0, []

    def run(self) -> None:
        from flhub_core.weights import from_safetensors

        weights = from_safetensors(Path(self.init_path).read_bytes())
        self.model = FLModel(params_type=ParamsType.FULL, params=weights)
        (Path(self.run_dir) / "rounds").mkdir(parents=True, exist_ok=True)
        self._save_round(0, weights, {"round": 0, "started_at": utc()})
        super().run()

    def send_model(self, task_name="train", data: FLModel = None, targets=None, min_responses=None, timeout=0,
                   callback=None):
        data.meta = {**(data.meta or {}), META_TRAIN_CONFIG: {**self.train_config, "round": data.current_round}}
        self._t_send, self._t_send_utc, self._replies = time.monotonic(), utc(), []
        return super().send_model(task_name=task_name, data=data, targets=targets, min_responses=min_responses,
                                  timeout=timeout, callback=callback)

    def _aggregate_one_result(self, result: FLModel) -> bool:
        meta = result.meta or {}
        self._replies.append({"site_id": meta.get("client_name"), "t_result_s": time.monotonic() - self._t_send,
                              "num_examples": meta.get("num_examples"), "metrics": dict(result.metrics or {}),
                              "client_code_version": meta.get("client_code_version", "unknown"),
                              "params": result.params})
        return super()._aggregate_one_result(result)

    def save_model(self, model: FLModel) -> None:
        """Called by FedAvg once per round with the new global model (timing ends here)."""
        duration = time.monotonic() - self._t_send
        sites = {}
        for r in sorted(self._replies, key=lambda r: r["site_id"] or ""):
            params = r.pop("params")
            ref = self._write_weights(f"r{model.current_round:04d}-{r['site_id']}", params,
                                      keep=self.keep_client_weights)
            sites[r["site_id"]] = {**r, "t_result_s": round(r["t_result_s"], 3), "weights_ref": ref}
        self._replies = []
        self._save_round(model.current_round, model.params,
                         {"round": model.current_round, "t_dispatch_utc": self._t_send_utc,
                          "duration_s": round(duration, 3), "sites": sites})

    def _write_weights(self, stem: str, params: dict, keep: bool = True) -> str:
        from flhub_core.weights import sha256_ref, to_safetensors

        data = to_safetensors(params)
        if keep:
            (Path(self.run_dir) / "rounds" / f"{stem}.safetensors").write_bytes(data)
        return sha256_ref(data)

    def _save_round(self, rnd: int, params: dict, record: dict) -> None:
        record["global_ref"] = self._write_weights(f"r{rnd:04d}", params)
        (Path(self.run_dir) / "rounds" / f"r{rnd:04d}.json").write_text(json.dumps(record, indent=1))
        self.info(f"native round {rnd}: global {record['global_ref'][:19]} ({record.get('duration_s', 0)} s)")


# ---- central side: build, submit, finalize ----------------------------------------------------
def load_run_config(path: str, name: str | None = None) -> dict:
    """The fields of a stage-2 run config (YAML/JSON) that the native run needs.

    train_config is what the hub dispatches (the yaml's train_config plus model and data_variant,
    which flhub_core 0.2 reads from it); sites are the run's NVFlare sites."""
    import yaml

    raw = yaml.safe_load(Path(path).read_text())
    tc = dict(raw.get("train_config") or {})
    for key in ("model", "data_variant"):
        if raw.get(key) is not None and key not in tc:
            tc[key] = raw[key]
    rcfg = {"name": name or raw.get("name") or Path(path).stem, "rounds": int(raw["rounds"]),
            "seed": int(raw.get("seed", tc.get("seed", 2026))), "sites": list(raw["sites"]), "train_config": tc,
            "deadline_s": float(raw.get("deadline_s", 1800)), "source": str(path)}
    rcfg["train_config"].setdefault("seed", rcfg["seed"])
    return rcfg


def build_native_job(run_dir: Path, rcfg: dict) -> Path:
    """Exports <run_dir>/job/flhub-native: NativeFedAvg on the server, the relay job's client code
    (client.py + flhub_core) at exactly the run's sites."""
    from nvflare.job_config.api import FedJob

    from adapters.nvflare import native  # not __main__: the class must keep its package path
    from adapters.nvflare.job import export_with_client_code

    sites = rcfg["sites"]
    job = FedJob(name=NATIVE_JOB, min_clients=len(sites))
    # NVFlare ships this module as the server app's custom code: a server app without custom code
    # is held to the kit's class allow-list, which does not list NativeFedAvg (BYOC is allowed).
    job.to_server(native.NativeFedAvg(num_clients=len(sites), num_rounds=rcfg["rounds"], start_round=1,
                               init_path=str(run_dir / "init.safetensors"), train_config=rcfg["train_config"],
                               run_dir=str(run_dir), keep_client_weights=not is_payload_run(rcfg)), id="fedavg")
    export_with_client_code(job, str(run_dir / "job"), targets=sites)
    return run_dir / "job" / NATIVE_JOB


def is_payload_run(rcfg: dict) -> bool:
    return str(rcfg["train_config"].get("model", "")).startswith("payload-")


def prepare(config: str, out: str, name: str | None = None) -> Path:
    import flhub_core
    from flhub_core.data import DATASETS
    from flhub_core.model import init_weights
    from flhub_core.train import TrainConfig
    from flhub_core.weights import sha256_ref, to_safetensors

    rcfg = load_run_config(config, name)
    run_dir = Path(out).expanduser().resolve()
    (run_dir / "rounds").mkdir(parents=True, exist_ok=False)
    tc = TrainConfig.from_dict(rcfg["train_config"])
    weights = init_weights(rcfg["seed"], tc.model, DATASETS[tc.dataset].num_classes)
    init = to_safetensors(weights)
    (run_dir / "init.safetensors").write_bytes(init)
    rcfg.update(mode="native", framework="nvflare", dataset=tc.dataset, init_ref=sha256_ref(init),
                flhub_core_version=flhub_core.code_version(), prepared_at=utc())
    (run_dir / "native_config.json").write_text(json.dumps(rcfg, indent=1))
    job_dir = build_native_job(run_dir, rcfg)
    print(job_dir)
    return job_dir


def submit(run_dir: str, admin_kit: str, timeout: float = 10800) -> str:
    """Submits the native job, waits for it to end; the job outcome goes to native_job.json."""
    from adapters.nvflare.flare_admin import FlareAdmin

    run_dir = Path(run_dir)
    admin = FlareAdmin(admin_kit)
    t0, rec = time.monotonic(), {"submitted_at": utc()}
    try:
        job_id = admin.submit_job(str(run_dir / "job" / NATIVE_JOB))
        rec["job_id"] = job_id
        status = "SUBMITTED"
        while time.monotonic() - t0 < timeout:
            status = admin.job_status(job_id)
            if status.startswith("FINISHED"):
                break
            time.sleep(2)
        else:
            admin.abort_job(job_id)
            status = f"ABORTED after timeout {timeout:.0f}s (was {status})"
        rec.update(status=status, ended_at=utc(), wall_s=round(time.monotonic() - t0, 1))
    finally:
        (run_dir / "native_job.json").write_text(json.dumps(rec, indent=1))
        admin.close()
    print(json.dumps(rec))
    return rec.get("status", "UNKNOWN")


def finalize(run_dir: str) -> dict:
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
            row.update({"round/duration_s": rec["duration_s"], "round/num_sites": len(rec["sites"]),
                        "round/num_examples": sum(int(s["num_examples"] or 0) for s in rec["sites"].values())})
            for site, s in sorted(rec["sites"].items()):
                row[f"site/{site}/num_examples"] = s["num_examples"]
                row[f"site/{site}/t_result_s"] = s["t_result_s"]
                for k, v in (s["metrics"] or {}).items():
                    row[f"site/{site}/{k}"] = v
                if "train_time_s" in (s["metrics"] or {}):
                    row[f"site/{site}/overhead_s"] = round(s["t_result_s"] - s["metrics"]["train_time_s"], 3)
        rows.append(row)
    (run_dir / "metrics.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    done = len(rows) - 1
    summary = {"status": "finished" if done == rcfg["rounds"] and job.get("status", "").startswith("FINISHED")
               else "failed", "mode": "native", "rounds": done, "job": job,
               "final/global_ref": rows[-1]["global_ref"] if rows else None, "run_dir": str(run_dir)}
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps({k: v for k, v in summary.items() if k != "job"}))
    return summary


def compare(native_dir: str, hub_dir: str) -> dict:
    """Per round: max abs difference native vs hub global weights (they evolve separately), and the
    one-step aggregation difference = hub FedAvg of the native round's own client results vs the
    native global (pure float32-vs-float64 aggregation effect). Writes native_vs_hub.json."""
    import numpy as np
    from flhub_core.weights import from_safetensors

    from hub.orchestrator.fedavg import fedavg

    nat, hub = Path(native_dir) / "rounds", Path(hub_dir) / "rounds"
    load = lambda p: from_safetensors(p.read_bytes())  # noqa: E731
    maxdiff = lambda a, b: max(float(np.max(np.abs(a[k] - b[k]))) for k in a)  # noqa: E731
    rows, rnd = [], 0
    while (nat / f"r{rnd:04d}.safetensors").exists():
        g = load(nat / f"r{rnd:04d}.safetensors")
        row = {"round": rnd}
        if (hub / f"r{rnd:04d}.safetensors").exists():
            row["global_max_abs_diff"] = maxdiff(g, load(hub / f"r{rnd:04d}.safetensors"))
        rec = json.loads((nat / f"r{rnd:04d}.json").read_text())
        clients = [(s, nat / f"r{rnd:04d}-{s}.safetensors", v["num_examples"]) for s, v in rec.get("sites", {}).items()]
        if clients and all(p.exists() for _, p, _ in clients):
            agg = fedavg([(s, load(p), int(n)) for s, p, n in clients], list(g))
            row["one_step_aggregation_diff"] = maxdiff(g, agg)
        rows.append(row)
        rnd += 1
    out = {"native": str(native_dir), "hub": str(hub_dir), "rounds": rows,
           "native_vs_hub_max_diff": max((r.get("global_max_abs_diff", 0.0) for r in rows), default=None)}
    (Path(native_dir) / "native_vs_hub.json").write_text(json.dumps(out, indent=1))
    print(json.dumps({k: v for k, v in out.items() if k != "rounds"}))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m adapters.nvflare.native")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--config", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--name", default=None)
    s = sub.add_parser("submit")
    s.add_argument("run_dir")
    s.add_argument("--admin-kit", required=True)
    s.add_argument("--timeout", type=float, default=10800)
    f = sub.add_parser("finalize")
    f.add_argument("run_dir")
    c = sub.add_parser("compare")
    c.add_argument("native_dir")
    c.add_argument("hub_dir")
    a = ap.parse_args(argv)
    if a.cmd == "prepare":
        prepare(a.config, a.out, a.name)
    elif a.cmd == "submit":
        status = submit(a.run_dir, a.admin_kit, a.timeout)
        raise SystemExit(0 if status.startswith("FINISHED:COMPLETED") else 1)
    elif a.cmd == "finalize":
        raise SystemExit(0 if finalize(a.run_dir)["status"] == "finished" else 1)
    else:
        compare(a.native_dir, a.hub_dir)


if __name__ == "__main__":
    main()

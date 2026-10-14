"""Finalize a stage-2 final run (PLAN.md stage 2 §6): gate -> W&B -> runs.md -> retention.

    python scripts/stage2/finalize_run.py <run_dir> --mode real [--replay] [--equal-to RUN_DIR]
    python scripts/stage2/finalize_run.py <run_dir> --mode sim --counterpart <real run name>
    python scripts/stage2/finalize_run.py <run_dir> --mode reference
    python scripts/stage2/finalize_run.py <run_dir> --mode native --hub-run RUN_DIR
           [--name N] [--exp E7] [--tags ...] [--notes "..."] [--dry-run] [--no-retention]

Gates:  real       INV-1 passed: <run_dir>/inv1.json (full sim) or inv1-replay.json (--replay);
                   --equal-to: final global_ref equal to that run (E6d resume)
        sim        the counterpart real run is in runs.md with INV-1 passed
        reference  finished, every metric finite
        native     finished; per-round max abs weight difference to the matching hub run recorded
On PASS: W&B run in project AgnosticFL-stage2 only (tag final, group E<n>), a row in
docs/results/evidence/stage2/runs.md, then retention (rounds 0, every 10th, last; replay-checked runs keep
0-3, last-1, last) and hub-core blob GC. On FAIL: nothing is uploaded and all files stay.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from flhub_core.weights import max_abs_diff  # noqa: E402

from hub.orchestrator.checkpoint import RunDir, retention_rounds, utc_now  # noqa: E402

PROJECT = "AgnosticFL-stage2"
FORBIDDEN_PROJECTS = {"AgnosticFL"}  # stage 1: read-only
RUNS_MD = REPO / "docs" / "results" / "evidence" / "stage2" / "runs.md"


class GateFailed(RuntimeError):
    pass


def read_json(path: Path) -> dict | None:
    return json.loads(path.read_text()) if path.exists() else None


def finite_rows(rows: list[dict]) -> bool:
    return all(math.isfinite(v) for r in rows for v in r.values() if isinstance(v, float))


def runs_md_rows() -> dict[str, dict]:
    out = {}
    if not RUNS_MD.exists():
        return out
    lines = [x for x in RUNS_MD.read_text().splitlines() if x.startswith("| ") and not x.startswith("| run ")]
    for line in lines:
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) >= 11 and not set(cells[0]) <= {"-"}:
            out[cells[0]] = {"exp": cells[1], "mode": cells[2], "inv1_method": cells[9], "inv1_max_diff": cells[10]}
    return out


def gate(rd: RunDir, args, cfg: dict, summary: dict) -> dict:
    """Returns the inv1 fields for the W&B summary, or raises GateFailed."""
    if not summary or summary.get("status") != "finished":
        raise GateFailed(f"run not finished: {summary and summary.get('status')}")
    rows = rd.metrics()
    if not finite_rows(rows):
        raise GateFailed("non-finite metrics")
    mode = args.mode
    if mode == "real":
        if args.equal_to:
            other = RunDir(args.equal_to)
            a, b = rd.record(rd.last_complete_round()), other.record(other.last_complete_round())
            if a["global_ref"] != b["global_ref"] or a["agg_state_ref"] != b["agg_state_ref"]:
                raise GateFailed(f"final weights/state differ from {other.path.name}")
            return {"inv1_method": f"resume-equal:{other.path.name.rsplit('-', 1)[0]}", "inv1_max_diff": 0.0,
                    "inv1_pass": True}
        res = read_json(rd.path / ("inv1-replay.json" if args.replay else "inv1.json"))
        if res is None:
            raise GateFailed("no INV-1 result: run hub.sim --compare <run_dir> (or --replay <run_dir>) first")
        if not res.get("pass"):
            raise GateFailed(f"INV-1 failed: max_abs_diff {res.get('max_abs_diff')}")
        part = cfg.get("participation") or {}
        partial = part.get("min_sites") is not None or float(part.get("fraction", 1.0)) < 1.0
        method = "replay" if args.replay else ("participants-sim" if partial else "full-sim")
        return {"inv1_method": method, "inv1_max_diff": float(res["max_abs_diff"]), "inv1_pass": True,
                "inv1_hashes_equal": bool(res.get("hashes_equal"))}
    if mode == "sim":
        if not args.counterpart:
            raise GateFailed("--counterpart <real run name> is required for sim runs")
        row = runs_md_rows().get(args.counterpart)
        if not row or row["mode"] != "real" or row["inv1_method"] in ("", "-"):
            raise GateFailed(f"counterpart {args.counterpart} has no INV-1-passed real row in runs.md")
        return {"inv1_method": f"sim-counterpart:{args.counterpart}", "inv1_max_diff": float(row["inv1_max_diff"]),
                "inv1_pass": True}
    if mode == "reference":
        return {"inv1_method": "reference", "inv1_max_diff": None, "inv1_pass": True}
    if mode == "native":
        if not args.hub_run:
            raise GateFailed("--hub-run <matching hub run dir> is required for native runs")
        hub = RunDir(args.hub_run)
        per_round, worst = [], 0.0
        for r in rd.recorded_rounds():
            if not (rd.weights_path(r).exists() and hub.weights_path(r).exists()):
                continue
            wa, wb = rd.load_weights(r), hub.load_weights(r)
            d = max_abs_diff(wa, {k: wb[k] for k in wa})
            per_round.append({"round": r, "max_abs_diff": d})
            worst = max(worst, d)
        if not per_round:
            raise GateFailed("no common round checkpoints with the hub run")
        # payload hub runs keep only the latest checkpoint; the per-round global hashes cover every round
        common = sorted(set(rd.recorded_rounds()) & set(hub.recorded_rounds()))
        equal = [r for r in common if rd.record(r).get("global_ref") == hub.record(r).get("global_ref")]
        rd.write_json("native_vs_hub.json", {"hub_run": str(hub.path), "per_round": per_round, "max": worst,
                                             "rounds_compared_by_hash": common, "hash_equal_rounds": equal})
        return {"inv1_method": "native", "native_vs_hub_max_diff": worst, "inv1_max_diff": None, "inv1_pass": True,
                "native_vs_hub_hash_equal": f"{len(equal)}/{len(common)}"}
    raise GateFailed(f"unknown mode {mode}")


def frozen_sha() -> str | None:
    p = Path.home() / "flhub-runtime" / "frozen_sha"
    return p.read_text().split()[0] if p.exists() else None


def wandb_config(cfg: dict) -> dict:
    keep = ("run_name", "kind", "mode", "experiment", "train_config", "rounds", "model_seed", "dataset",
            "dataset_sha256", "data_variant", "partition_manifest_sha256", "partition_sizes", "model", "model_params",
            "aggregator", "participation", "deadline_s", "round_retries", "netem", "sites", "framework_versions",
            "protocols", "protocol", "git_sha", "git_dirty", "flhub_core_version", "torch_version", "python_version",
            "deploy_versions", "config_file")
    return {**{k: cfg[k] for k in keep if k in cfg}, "frozen_sha": frozen_sha()}


def tags_for(cfg: dict, exp: str, mode: str, extra: list[str]) -> list[str]:
    rc = cfg.get("run_config", {})
    tags = ["final", exp, f"mode:{mode}"]
    if rc.get("partition"):
        tags.append(f"part:{rc['partition']}")
    if rc.get("aggregator", {}).get("name"):
        tags.append(f"agg:{rc['aggregator']['name']}")
    if rc.get("model"):
        tags.append(f"model:{rc['model']}")
    return tags + [t for t in extra if t not in tags]


def upload(rd: RunDir, name: str, exp: str, tags: list[str], cfg: dict, summary: dict, dry: bool,
           wandb_mode: str = "online") -> tuple:
    project = os.environ.get("WANDB_PROJECT", PROJECT)
    if project != PROJECT or project in FORBIDDEN_PROJECTS:
        raise SystemExit(f"refusing to upload to W&B project {project!r}; stage-2 runs go to {PROJECT} only")
    if dry:
        return None, None
    import wandb

    with tempfile.TemporaryDirectory() as tmp:
        run = wandb.init(project=PROJECT, entity=os.environ.get("WANDB_ENTITY") or None, name=name, group=exp,
                         tags=tags, config=wandb_config(cfg), job_type=cfg.get("mode", "real"), dir=tmp,
                         mode=wandb_mode, reinit=True, settings=wandb.Settings(console="off", silent=True))
        for row in rd.metrics():
            step = row["step"]
            vals = {k: v for k, v in row.items() if k != "step" and isinstance(v, (int, float))
                    and not isinstance(v, bool)}
            run.log(vals, step=step)
        run.summary.update(summary)
        run_id, url = run.id, run.url
        run.finish()
    return run_id, url


def fmt(v, nd=4) -> str:
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:.{nd}f}" if abs(v) >= 1e-3 or v == 0 else f"{v:.2e}"
    return str(v)


def append_row(name: str, exp: str, mode: str, url: str | None, summary: dict, notes: str) -> None:
    acc = summary.get("final/test_acc", summary.get("final/mean_acc"))
    row = (f"| {name} | {exp} | {mode} | {f'[run]({url})' if url else '-'} | {summary.get('rounds', '-')} | "
           f"{fmt(acc)} | {fmt(summary.get('final/test_bal_acc'))} | {fmt(summary.get('final/test_auc'))} | "
           f"{fmt(summary.get('round_time_median_s'), 2)} | {summary.get('inv1_method', '-')} | "
           f"{fmt(summary.get('inv1_max_diff') if summary.get('inv1_max_diff') is not None else summary.get('native_vs_hub_max_diff'), 3)} | {notes or ''} |")
    with open(RUNS_MD, "a") as f:
        f.write(row + "\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="finalize_run.py")
    ap.add_argument("run_dir")
    ap.add_argument("--mode", required=True, choices=["real", "sim", "reference", "native"])
    ap.add_argument("--replay", action="store_true", help="real: INV-1 by replay spot-check")
    ap.add_argument("--equal-to", help="real (E6d): final weights must equal this run's")
    ap.add_argument("--counterpart", help="sim: name of the real seed-2026 run of the same config")
    ap.add_argument("--hub-run", help="native: the matching hub run dir")
    ap.add_argument("--name")
    ap.add_argument("--exp")
    ap.add_argument("--tags", nargs="*", default=[])
    ap.add_argument("--notes", default="")
    ap.add_argument("--dry-run", action="store_true", help="gate only; no W&B, no runs.md, no retention")
    ap.add_argument("--no-retention", action="store_true")
    ap.add_argument("--hub", default="http://127.0.0.1:8800")
    ap.add_argument("--wandb-mode", default="online", choices=["online", "offline"],
                    help="offline only to test this script (writes a local W&B dir, nothing uploaded)")
    args = ap.parse_args(argv)
    rd = RunDir(args.run_dir)
    if (rd.path / "finalized.json").exists():
        print(f"{rd.path.name} is already finalized: {(rd.path / 'finalized.json').read_text()}")
        return 1
    cfg = read_json(rd.path / "config.json") or {}
    summary = read_json(rd.path / "summary.json") or {}
    name = args.name or cfg.get("run_name") or rd.path.name.rsplit("-", 1)[0]
    exp = args.exp or cfg.get("experiment") or "E0"
    try:
        inv1 = gate(rd, args, cfg, summary)
    except GateFailed as exc:
        print(f"GATE FAIL {name}: {exc} (nothing uploaded, files kept)")
        return 1
    print(f"GATE PASS {name}: {inv1}")
    full = {**summary, **inv1, "finalized_utc": utc_now()}
    if args.dry_run:
        return 0
    run_id, url = upload(rd, name, exp, tags_for(cfg, exp, args.mode, args.tags), {**cfg, "mode": args.mode}, full,
                         False, args.wandb_mode)
    if args.wandb_mode != "online":
        print(f"offline test upload of {name} ok ({run_id}); runs.md, retention and finalized.json untouched")
        return 0
    append_row(name, exp, args.mode, url, full, args.notes)
    freed = 0
    last = rd.last_complete_round()
    if not args.no_retention and last is not None:
        freed = rd.apply_retention(retention_rounds(last, replay=args.replay))
    gc = None
    try:
        from adapters.common import HubClient

        gc = HubClient(args.hub).gc(older_than_s=60)
    except Exception as exc:
        print(f"blob GC skipped: {exc}")
    rd.write_json("finalized.json", {"name": name, "exp": exp, "mode": args.mode, "wandb_id": run_id,
                                      "wandb_url": url, "utc": utc_now(), **inv1, "retention_freed_bytes": freed,
                                      "blob_gc": gc})
    print(f"finalized {name}: W&B {url}; runs.md row added; retention freed {freed / 2**20:.1f} MB; gc {gc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

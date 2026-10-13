"""Keep only finished runs tagged `final` in the W&B project (COLLABORATION.md §6).

    python scripts/wandb_cleanup.py [--apply] [--project AgnosticFL] [--entity E] [--tag final] [--dedupe]

Dry run by default: lists every run with KEEP/DELETE. --apply deletes. --dedupe also deletes older
finished `final` runs that share a name with a newer one. Prints the runs that remain.
"""

from __future__ import annotations

import argparse
import os
import sys


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="wandb_cleanup.py")
    ap.add_argument("--apply", action="store_true", help="really delete (default: dry run)")
    ap.add_argument("--project", default=os.environ.get("WANDB_PROJECT", "AgnosticFL"))
    ap.add_argument("--entity", default=os.environ.get("WANDB_ENTITY") or None)
    ap.add_argument("--tag", default="final")
    ap.add_argument("--dedupe", action="store_true")
    args = ap.parse_args(argv)
    import wandb

    api = wandb.Api()
    entity = args.entity or api.default_entity
    runs = sorted(api.runs(f"{entity}/{args.project}"), key=lambda r: r.created_at, reverse=True)
    seen, keep, drop = set(), [], []
    for r in runs:
        ok = args.tag in (r.tags or []) and r.state == "finished"
        if ok and args.dedupe and r.name in seen:
            ok = False
        (keep if ok else drop).append(r)
        if ok:
            seen.add(r.name)
    print(f"project {entity}/{args.project}: {len(runs)} runs, keep {len(keep)}, delete {len(drop)}"
          f"{'' if args.apply else ' (dry run)'}")
    for r in drop:
        print(f"  DELETE {r.name:28s} {r.id:10s} state={r.state:9s} tags={r.tags}")
        if args.apply:
            r.delete()
    print("remaining:")
    for r in keep:
        acc = r.summary.get("final/test_acc")
        print(f"  KEEP   {r.name:28s} {r.id:10s} state={r.state:9s} final/test_acc={acc} {r.url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

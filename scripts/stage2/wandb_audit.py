"""Read-only W&B audit for the stage-2 Definition of Done (PLAN.md stage 2 §9.5).

    python scripts/stage2/wandb_audit.py [--out docs/report/stage2/evidence/wandb_audit.json]

Lists every run of project AgnosticFL-stage2 and checks: state finished, tag `final`, group and tag
E<n> matching the name (s2-e<n>[a-e]-...), unique names, and that the set of names equals the rows of
docs/report/stage2/runs.md. Counts the runs of the stage-1 project AgnosticFL (must stay 6). Never
writes to or deletes from W&B. Exit 0 only if every check passes.
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
RUNS_MD = REPO / "docs" / "report" / "stage2" / "runs.md"
OUT = REPO / "docs" / "report" / "stage2" / "evidence" / "wandb_audit.json"
NAME = re.compile(r"^s2-e(\d)[a-e]?-[a-z0-9.\-]+$")


def runs_md_names() -> list[str]:
    return [line.split("|")[1].strip() for line in RUNS_MD.read_text().splitlines() if line.startswith("| s2-")]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args(argv)
    import wandb

    api = wandb.Api()
    entity = os.environ.get("WANDB_ENTITY") or api.default_entity
    s2 = list(api.runs(f"{entity}/AgnosticFL-stage2"))
    s1 = list(api.runs(f"{entity}/AgnosticFL"))
    problems = []
    names = [r.name for r in s2]
    for r in s2:
        m = NAME.match(r.name or "")
        exp = f"E{m.group(1)}" if m else None
        if exp is None:
            problems.append(f"{r.name}: name does not match s2-e<n>-...")
        if r.state != "finished":
            problems.append(f"{r.name}: state {r.state}")
        if "final" not in (r.tags or []):
            problems.append(f"{r.name}: no tag final")
        if exp and r.group != exp:
            problems.append(f"{r.name}: group {r.group}, expected {exp}")
        if exp and exp not in (r.tags or []):
            problems.append(f"{r.name}: no tag {exp}")
    dups = sorted(n for n, c in collections.Counter(names).items() if c > 1)
    rows = runs_md_names()
    res = {
        "utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "entity": entity,
        "stage2_project": "AgnosticFL-stage2",
        "stage2_runs": len(s2),
        "stage2_by_group": dict(sorted(collections.Counter(r.group for r in s2).items())),
        "stage2_by_mode": dict(sorted(collections.Counter(
            next((t[5:] for t in (r.tags or []) if t.startswith("mode:")), "?") for r in s2).items())),
        "runs_md_rows": len(rows),
        "duplicates": dups,
        "in_runs_md_not_in_wandb": sorted(set(rows) - set(names)),
        "in_wandb_not_in_runs_md": sorted(set(names) - set(rows)),
        "problems": problems,
        "stage1_project": "AgnosticFL",
        "stage1_runs": len(s1),
        "stage1_names": sorted(r.name for r in s1),
    }
    res["ok"] = (not problems and not dups and not res["in_runs_md_not_in_wandb"]
                 and not res["in_wandb_not_in_runs_md"] and len(s1) == 6)
    Path(args.out).write_text(json.dumps(res, indent=1) + "\n")
    print(json.dumps({k: v for k, v in res.items() if k != "stage1_names"}, indent=1))
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

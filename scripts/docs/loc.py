"""Lines of code per component (docs tooling; reads the repository, writes only under docs/).

    python scripts/docs/loc.py [--out docs/results/evidence/loc.md] [--json]

Code lines = non-blank, non-comment lines. Python is tokenized, so a `#` inside a string does not
make a comment and docstrings count as code (they are not comments); shell scripts, `.env` files
and Dockerfiles count every non-blank line that does not start with `#`. TOML and YAML files are
counted separately as configuration lines. The component list below is the one used in
docs/cost-and-efficiency.md; every component names its paths, so the counts can be re-checked.
"""

from __future__ import annotations

import argparse
import io
import json
import tokenize
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "docs" / "results" / "evidence" / "loc.md"

# (group, component, paths or globs relative to the repository root, excluded paths)
COMPONENTS: list[tuple[str, str, list[str], list[str]]] = [
    ("hub", "hub core (blob store, model spec)", ["hub/core/*.py"], []),
    ("hub", "orchestrator (rounds, validation, checkpoints)", ["hub/orchestrator/*.py"], []),
    ("hub", "aggregators (6 server strategies)", ["hub/aggregators/*.py"], []),
    ("hub", "simulation (hub.sim)", ["hub/sim/*.py"], []),
    ("hub", "adapter base (adapters/common)", ["adapters/common/*.py"], []),
    ("adapter", "NVFlare adapter + relay controller", ["adapters/nvflare/*.py"], ["adapters/nvflare/native.py"]),
    ("adapter", "vantage6 adapter", ["adapters/vantage6/*.py"], ["adapters/vantage6/native.py"]),
    ("adapter", "Flower adapter + relay ServerApp", ["adapters/flower/*.py", "clients/flower/flhub_flower/server_app.py"], []),
    ("adapter", "mock adapter (no framework)", ["adapters/mock/*.py"], []),
    ("library", "flhub_core (shared training library)", ["flhub_core/src/flhub_core/*.py"], []),
    ("client", "NVFlare client (Client API script)", ["clients/nvflare/*.py"], []),
    ("client", "vantage6 client (algorithm package + image)", ["clients/vantage6/v6_flhub/*.py", "clients/vantage6/Dockerfile",
                                                            "clients/vantage6/pyproject.toml"], []),
    ("client", "Flower client (ClientApp)", ["clients/flower/flhub_flower/client_app.py", "clients/flower/flhub_flower/records.py",
                                            "clients/flower/flhub_flower/__init__.py", "clients/flower/pyproject.toml"], []),
    ("baseline", "native FedAvg baselines (E7 only)", ["adapters/nvflare/native.py", "adapters/vantage6/native.py",
                                                       "deploy/central/*/native/**/*"], []),
    ("tests", "unit tests", ["tests/unit/*.py"], []),
    ("tests", "conformance suite", ["tests/conformance/*.py"], []),
    ("tests", "Flower relay tests", ["tests/flower/*.py"], []),
    ("deploy", "deploy: central (hub, registry)", ["deploy/central/*.sh", "deploy/*.env"], []),
    ("deploy", "deploy: NVFlare (central + hospital VM)", ["deploy/central/nvflare/*", "deploy/nvflare-vm/**/*"], []),
    ("deploy", "deploy: vantage6 (central + hospital VM)", ["deploy/central/vantage6/*", "deploy/vantage6-vm/**/*"], []),
    ("deploy", "deploy: Flower (central + hospital VM)", ["deploy/central/flower/*", "deploy/flower-vm/**/*"], []),
    ("scripts", "scripts: equivalence check, sysmon, W&B clean-up", ["scripts/*.py", "scripts/*.sh"], []),
    ("scripts", "scripts: stage-2 campaign and report tooling", ["scripts/stage2/*"], []),
    ("scripts", "scripts: docs tooling", ["scripts/docs/*.py"], []),
]

CONFIG_SUFFIXES = {".toml", ".yaml", ".yml"}


def _python_lines(text: str) -> int:
    """Lines that carry at least one token other than a comment (multi-line strings count fully)."""
    code_lines: set[int] = set()
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type in (tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT,
                            tokenize.ENDMARKER):
                continue
            code_lines.update(range(tok.start[0], tok.end[0] + 1))
    except (tokenize.TokenError, IndentationError):
        return _hash_comment_lines(text)
    return len(code_lines)


def _hash_comment_lines(text: str) -> int:
    return sum(1 for line in text.splitlines() if line.strip() and not line.strip().startswith("#"))


def count_file(path: Path) -> tuple[str, int]:
    """('code' | 'config' | 'other', counted lines) for one file."""
    text = path.read_text(encoding="utf-8", errors="replace")
    if path.suffix == ".py":
        return "code", _python_lines(text)
    if path.suffix in (".sh", ".env") or path.name.startswith("Dockerfile") or text.startswith("#!"):
        return "code", _hash_comment_lines(text)
    if path.suffix in CONFIG_SUFFIXES:
        return "config", _hash_comment_lines(text)
    return "other", 0


def _expand(patterns: list[str]) -> list[Path]:
    out: list[Path] = []
    for pat in patterns:
        out.extend(p for p in sorted(REPO.glob(pat)) if p.is_file() and "__pycache__" not in p.parts)
    return out


def count() -> list[dict]:
    rows = []
    for group, name, patterns, exclude in COMPONENTS:
        excluded = {REPO / e for e in exclude}
        files = [p for p in dict.fromkeys(_expand(patterns)) if p not in excluded]
        code = config = 0
        for p in files:
            kind, n = count_file(p)
            code += n if kind == "code" else 0
            config += n if kind == "config" else 0
        rows.append({"group": group, "component": name, "files": len(files), "code_lines": code,
                     "config_lines": config, "paths": patterns, "excluded": exclude})
    return rows


def to_markdown(rows: list[dict]) -> str:
    out = ["# Lines of code per component (generated by scripts/docs/loc.py)", "",
           "Code lines are non-blank, non-comment lines (Python: tokenized, docstrings count as code; shell, "
           "`.env` and Dockerfiles: lines not starting with `#`). Configuration lines are non-blank, non-comment "
           "TOML/YAML lines. Counted on the files listed in the last column.", "",
           "| group | component | files | code lines | config lines | paths |", "|---|---|---|---|---|---|"]
    for r in rows:
        paths = ", ".join(f"`{p}`" for p in r["paths"])
        if r["excluded"]:
            paths += " (without " + ", ".join(f"`{e}`" for e in r["excluded"]) + ")"
        out.append(f"| {r['group']} | {r['component']} | {r['files']} | {r['code_lines']} | {r['config_lines']} | {paths} |")
    totals: dict[str, int] = {}
    for r in rows:
        totals[r["group"]] = totals.get(r["group"], 0) + r["code_lines"]
    out += ["", "Code lines per group: " + ", ".join(f"{g} {n}" for g, n in totals.items()) + "."]
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="scripts/docs/loc.py")
    ap.add_argument("--out", default=str(OUT), help="Markdown table to write (must be under docs/)")
    ap.add_argument("--json", action="store_true", help="print the rows as JSON instead of writing the table")
    args = ap.parse_args(argv)
    rows = count()
    if args.json:
        print(json.dumps(rows, indent=1))
        return 0
    out = Path(args.out).resolve()
    if REPO / "docs" not in out.parents:
        ap.error("--out must be under docs/")
    out.write_text(to_markdown(rows))
    for r in rows:
        print(f"{r['code_lines']:>6} {r['config_lines']:>5}  {r['component']}")
    print(f"wrote {out.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

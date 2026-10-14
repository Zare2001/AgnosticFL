"""Check every relative link and image in the repository's Markdown files (docs tooling, read-only).

    python scripts/docs/check_links.py [--root .] [--figures docs/results/figures] [-v]

For every Markdown file that git tracks or would track (`git ls-files -co --exclude-standard`):
inline links and images `[text](target)` / `![alt](target)`, reference definitions `[id]: target`
and HTML `<img src>` / `<a href>`. Fenced code blocks and inline code are ignored. External
targets (http, https, mailto) are not fetched. A relative target must exist; a `#anchor` must
match a heading of the target Markdown file (GitHub's slug rules). Finally, every file in the
figures directory must be referenced by at least one Markdown file. Exit status 1 on any problem.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote

INLINE = re.compile(r"!?\[(?:[^\[\]]|\[[^\]]*\])*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
REFDEF = re.compile(r"^\s{0,3}\[[^\]]+\]:\s*<?(\S+?)>?(?:\s+.*)?$")
HTML = re.compile(r"<(?:img|a)\b[^>]*?\b(?:src|href)\s*=\s*[\"']([^\"']+)[\"']", re.I)
FENCE = re.compile(r"^\s{0,3}(```|~~~)")
CODE_SPAN = re.compile(r"(`+)(?:(?!\1).)+?\1")
HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
EXTERNAL = ("http://", "https://", "mailto:", "ftp://")


def markdown_files(root: Path) -> list[Path]:
    out = subprocess.run(["git", "-C", str(root), "ls-files", "-co", "--exclude-standard", "*.md"],
                         capture_output=True, text=True, check=True).stdout.split("\n")
    return sorted(root / line for line in out if line.strip())


def strip_code(lines: list[str]) -> list[tuple[int, str]]:
    """(line number, text) of the lines outside fenced code blocks, with inline code removed."""
    out, fence = [], None
    for i, line in enumerate(lines, 1):
        m = FENCE.match(line)
        if m:
            fence = None if fence == m.group(1) else (fence or m.group(1))
            continue
        if fence is None:
            out.append((i, CODE_SPAN.sub("", line)))
    return out


def slug(text: str) -> str:
    """GitHub's heading anchor: lower case, markup and punctuation removed, spaces to hyphens."""
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)  # links keep their text
    text = re.sub(r"<[^>]+>", "", text).replace("`", "").lower()
    text = re.sub(r"[^\w\- ]", "", text, flags=re.UNICODE)
    return text.replace(" ", "-")


def anchors(path: Path, cache: dict[Path, set[str]]) -> set[str]:
    if path not in cache:
        seen: dict[str, int] = {}
        found = set()
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        fence = None
        for line in lines:
            m = FENCE.match(line)
            if m:
                fence = None if fence == m.group(1) else (fence or m.group(1))
                continue
            h = HEADING.match(line) if fence is None else None
            if h:
                base = slug(h.group(2))
                n = seen.get(base, 0)
                found.add(base if n == 0 else f"{base}-{n}")
                seen[base] = n + 1
        found.update(re.findall(r"<a\s+(?:name|id)=\"([^\"]+)\"", "\n".join(lines)))
        cache[path] = found
    return cache[path]


def targets(path: Path) -> list[tuple[int, str]]:
    found = []
    for no, text in strip_code(path.read_text(encoding="utf-8", errors="replace").splitlines()):
        for rx in (INLINE, HTML):
            found.extend((no, m.group(1)) for m in rx.finditer(text))
        m = REFDEF.match(text)
        if m:
            found.append((no, m.group(1)))
    return found


def check(root: Path, figures: Path, verbose: bool = False) -> int:
    problems: list[str] = []
    referenced: set[Path] = set()
    cache: dict[Path, set[str]] = {}
    files = markdown_files(root)
    n_links = 0
    for md in files:
        for no, target in targets(md):
            if target.startswith(EXTERNAL):
                continue
            n_links += 1
            where = f"{md.relative_to(root)}:{no}"
            raw_path, _, anchor = target.partition("#")
            raw_path = unquote(raw_path.split("?", 1)[0])
            if raw_path.startswith("/"):
                problems.append(f"{where}: absolute link {target!r} (use a relative path)")
                continue
            dest = (md.parent / raw_path).resolve() if raw_path else md
            if not dest.exists():
                problems.append(f"{where}: missing target {target!r}")
                continue
            referenced.add(dest)
            if anchor and dest.suffix == ".md" and anchor not in anchors(dest, cache):
                problems.append(f"{where}: no heading for anchor {target!r}")
            elif verbose:
                print(f"ok  {where}: {target}")
    unreferenced = [p for p in sorted(figures.glob("*")) if p.is_file() and p.resolve() not in referenced]
    for p in unreferenced:
        problems.append(f"{p.relative_to(root)}: figure is not referenced by any Markdown file")
    for line in problems:
        print(line)
    print(f"checked {n_links} relative links in {len(files)} Markdown files; "
          f"{len(problems)} problem(s); {len(list(figures.glob('*')))} figures, {len(unreferenced)} unreferenced")
    return 1 if problems else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="scripts/docs/check_links.py")
    ap.add_argument("--root", default=str(Path(__file__).resolve().parents[2]))
    ap.add_argument("--figures", default="docs/results/figures")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    root = Path(args.root).resolve()
    return check(root, (root / args.figures).resolve(), args.verbose)


if __name__ == "__main__":
    sys.exit(main())

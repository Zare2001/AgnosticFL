#!/usr/bin/env bash
# Operator, central machine: your own models and datasets as plugins, without changing the code of the
# repository. A plugin is a .py file (see flhub_core/src/flhub_core/plugins/_example_my_net.py); this
# script puts your files into flhub_core/plugins/ (not tracked by git), where every framework's channel
# picks them up at the next rollout.
#   bash deploy/central/plugins.sh DIR       # replace the installed plugins with DIR/*.py, then check them
#   bash deploy/central/plugins.sh           # list the installed plugins, models, datasets, code version
#   bash deploy/central/plugins.sh --clear   # remove all plugins
# Then: bash deploy/central/rollout.sh (or rollout.sh --plugins DIR, which runs this script first).
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
PDIR="$REPO/flhub_core/src/flhub_core/plugins"
PY="${FLHUB_PY:-$HOME/venvs/hub/bin/python}"
[ -x "$PY" ] || PY="$REPO/.venv/bin/python"
[ -x "$PY" ] || { echo "plugins: no Python with torch (set FLHUB_PY=/path/to/python)" >&2; exit 1; }

clear_plugins() {  # everything but the package's own files
  find "$PDIR" -maxdepth 1 -type f -name '*.py' ! -name '__init__.py' ! -name '_example_*.py' -delete
  rm -rf "$PDIR/__pycache__"
}

case "${1:-}" in
  "") ;;
  --clear) clear_plugins; echo "plugins: removed" ;;
  -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
  *)
    src="$1"
    [ -d "$src" ] || { echo "plugins: $src is not a directory" >&2; exit 1; }
    compgen -G "$src/*.py" >/dev/null || { echo "plugins: no .py files in $src" >&2; exit 1; }
    clear_plugins
    for f in "$src"/*.py; do
      name="$(basename "$f")"
      case "$name" in
        __init__.py|_*) echo "plugins: skipped $name (a name starting with _ is not loaded)" ;;
        *) cp "$f" "$PDIR/$name"; echo "plugins: installed $name" ;;
      esac
    done ;;
esac

cd "$REPO"
PYTHONPATH="$REPO/flhub_core/src" "$PY" - <<'PY'
import sys
import flhub_core
from flhub_core import data as D, model as M, plugins as P
print("plugins:  " + (", ".join(P.names()) or "none"))
print("models:   " + ", ".join(sorted(M._ZOO)))
print("datasets: " + ", ".join(f"{n} ({'x'.join(map(str, i.image_shape))}, {i.num_classes} classes)"
                               for n, i in sorted(D.DATASETS.items())))
print("flhub_core " + flhub_core.code_version())
if P.failed:
    print("NOT LOADED: " + "; ".join(f"{k}: {v}" for k, v in P.failed.items()), file=sys.stderr)
    sys.exit(1)
PY

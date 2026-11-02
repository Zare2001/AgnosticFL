"""Your own models and datasets, without changing flhub_core's code.

Put a Python file into this folder on the operator's machine (its contents are not tracked by git;
`bash deploy/central/plugins.sh <your folder>` copies your files here). A plugin is part of
flhub_core from then on: it reaches every hospital with flhub_core through each framework's own
channel (`bash deploy/central/rollout.sh`), and it is part of flhub_core.code_version(), which the hub
compares with every site's (`strict_code_version: true`). A plugin registers what it defines:

    from flhub_core.model import register_model                   # a model (needs torch)
    from flhub_core.data import DatasetInfo, register_dataset     # a dataset (numpy only)

Put a dataset in a file of its own, without torch: the hospitals' data tool runs with numpy only.
Files whose name starts with "_" are not loaded; _example_my_net.py is a model to copy.
"""

from __future__ import annotations

import importlib
import pkgutil

_loaded = False
failed: dict[str, str] = {}  # plugin module -> why it could not be imported here (e.g. no torch)


def names() -> list[str]:
    """The plugin modules of this folder, in load order."""
    return sorted(m.name for m in pkgutil.iter_modules(__path__) if not m.name.startswith("_"))


def load() -> None:
    """Imports every plugin module once, in name order. A module that cannot be imported in this
    environment (a model plugin in the numpy-only data tool) is skipped and recorded in `failed`."""
    global _loaded
    if _loaded:
        return
    _loaded = True
    for name in names():
        try:
            importlib.import_module(f"{__name__}.{name}")
        except ImportError as exc:
            failed[name] = f"{type(exc).__name__}: {exc}"

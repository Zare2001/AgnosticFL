"""flhub_core: the training code shared by every hospital client and by the hub.

All frameworks run exactly this code at the hospitals, which is what makes plain
FedAvg across NVFlare, vantage6 and Flower valid.
"""

from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path

__version__ = "0.2.0"


@lru_cache(maxsize=1)
def code_version() -> str:
    """`<version>+<sha256 prefix of this package's source files>`.

    Clients report it in their replies (envelope `client_code_version`) so the hub
    can verify that every site ran byte-identical training code. Needs no git. Hashes every
    .py file of the package (relative path + bytes, sorted); for a flat package this is the
    stage-1 definition.
    """
    h = hashlib.sha256()
    pkg = Path(__file__).resolve().parent
    files = [f for f in pkg.rglob("*.py") if "__pycache__" not in f.parts]
    for f in sorted(files, key=lambda f: f.relative_to(pkg).as_posix()):
        h.update(f.relative_to(pkg).as_posix().encode())
        h.update(b"\0")
        h.update(f.read_bytes())
        h.update(b"\0")
    return f"{__version__}+{h.hexdigest()[:12]}"

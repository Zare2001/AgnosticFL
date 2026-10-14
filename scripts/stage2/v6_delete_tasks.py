"""Delete every vantage6 task on central by id, without listing tasks through the API.

    ~/venvs/v6/bin/python scripts/stage2/v6_delete_tasks.py [--dry-run]

deploy/central/vantage6/purge.sh lists the tasks with GET /task before deleting them; with large run
rows (E3 cnn-medium: 2 x 12 MB per run) the server's single worker spends many minutes building that
response, the client times out and retries, and the nodes lose their websocket. This helper reads the
task ids and run states read-only from the server's sqlite file, refuses while a run is still open,
and calls DELETE /task/<id> as root (the same API call purge.sh makes). Afterwards
`purge.sh --vacuum` finds no tasks and only vacuums.
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
import time
from pathlib import Path

RT = Path.home() / "flhub-runtime" / "vantage6"
DB = Path.home() / ".local" / "share" / "vantage6" / "server" / "flhub" / "flhub.sqlite"
OPEN = ("pending", "initializing", "active")


def value(path: Path, key: str) -> str:
    for line in path.read_text().splitlines():
        if line.startswith(key + "="):
            return line.split("=", 1)[1].strip()
    raise SystemExit(f"{key} missing in {path}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=10)
    ids = [r[0] for r in db.execute("select id from task order by id")]
    open_runs = [r for r in db.execute("select task_id, status from run") if (r[1] or "").lower() in OPEN]
    db.close()
    print(f"{len(ids)} tasks; open runs: {open_runs}")
    if open_runs:
        print("refusing: runs still open")
        return 1
    if args.dry_run or not ids:
        return 0
    from vantage6.client import UserClient

    root = logging.getLogger()
    handlers = list(root.handlers)
    client = UserClient(value(RT / "adapter.env", "V6_SERVER_URL"), int(value(RT / "adapter.env", "V6_SERVER_PORT")),
                        "/api", log_level="warn")
    root.handlers[:] = handlers
    client.log = logging.getLogger("v6-delete")
    client.log.setLevel(logging.ERROR)
    client.authenticate("root", value(RT / "secrets.env", "V6_ROOT_PASSWORD"))
    client.setup_encryption(None)
    t0 = time.monotonic()
    for i in ids:
        client.request(f"task/{i}", method="delete")
    print(f"deleted {len(ids)} tasks in {time.monotonic() - t0:.1f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())

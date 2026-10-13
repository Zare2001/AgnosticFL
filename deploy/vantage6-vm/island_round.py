"""Development helper (island only): dispatch hub rounds to an adapter and report each task's
outcome and timing, e.g. to exercise deploy/vantage6-vm/inject.sh and straggler.sh.

    ~/venvs/island/bin/python deploy/vantage6-vm/island_round.py --rounds 3 4 [--sites v6-h3 v6-h4]
        [--adapter http://127.0.0.1:8812] [--deadline 600]
"""

from __future__ import annotations

import argparse
import time
from datetime import datetime, timezone

from adapters.common import AdapterClient, HubClient
from flhub_core.model import init_weights
from flhub_core.weights import spec

TRAIN_CONFIG = {"local_epochs": 1, "batch_size": 64, "lr": 0.05, "momentum": 0.0, "seed": 2026}


def utc() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:-3] + "Z"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, nargs="+", required=True)
    ap.add_argument("--sites", nargs="+", default=["v6-h3", "v6-h4"])
    ap.add_argument("--adapter", default="http://127.0.0.1:8812")
    ap.add_argument("--deadline", type=float, default=600)
    args = ap.parse_args()
    client, hub = AdapterClient(args.adapter), HubClient()
    w = init_weights(2026)
    hub.put_spec(spec(w))
    ref = hub.put_weights(w)
    for rnd in args.rounds:
        t0 = time.monotonic()
        task_id = client.dispatch(rnd, {"ref": ref}, TRAIN_CONFIG, args.sites, args.deadline)
        print(f"{utc()} round {rnd}: dispatched {task_id}", flush=True)
        last = None
        while True:
            st = client.get_task(task_id)
            view = (st["state"], st["progress"]["done"], str(st.get("sites")))
            if view != last:
                print(f"{utc()}   {st['state']} {st['progress']} sites={st.get('sites')} error={st.get('error')}",
                      flush=True)
                last = view
            if st["state"] in ("completed", "failed", "cancelled"):
                break
            time.sleep(0.5)
        if st["state"] == "completed":
            envs = client.collect(task_id)
            print(f"{utc()}   collected {[(e['site_id'], e['num_examples']) for e in envs]}", flush=True)
        print(f"{utc()} round {rnd}: {st['state']} after {time.monotonic() - t0:.1f}s", flush=True)


if __name__ == "__main__":
    main()

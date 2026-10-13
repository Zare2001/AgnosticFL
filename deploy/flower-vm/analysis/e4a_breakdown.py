"""E4a Flower breakdown from hub run dirs (metrics.jsonl), medians over rounds >= 2 (round 1 = warm-up).

    python3 -I e4a_breakdown.py <dir with s2-e4a-<scope>-<S>mb-* run dirs> [--sites flw-h5 flw-h6]

Per site and round (relay timings in the site metrics, 66e806e): prep (relay: job -> model ready),
push (TRAIN message to the SuperLink), reply (pushed -> reply pulled), store (reply -> hub core);
flower_s = reply - train - data load = SuperLink -> SuperNode download, ClientApp start, upload, pull;
rest_s = t_result - prep - push - reply - store = adapter and hub-core side outside the relay.
"""
import json
import re
import statistics as st
import sys
from pathlib import Path


def med(xs):
    xs = [x for x in xs if x is not None]
    return round(st.median(xs), 2) if xs else None


def main():
    root = Path(sys.argv[1])
    sites = sys.argv[sys.argv.index("--sites") + 1:] if "--sites" in sys.argv else ["flw-h5", "flw-h6"]
    rows = []
    for run in sorted(root.glob("s2-e4a-*mb-*"), key=lambda p: int(re.search(r"-(\d+)mb-", p.name)[1])):
        recs = [json.loads(x) for x in (run / "metrics.jsonl").read_text().splitlines() if x.strip()]
        recs = [r for r in recs if r.get("step", 0) >= 2]

        def site(name):
            return [r.get(f"site/{s}/{name}") for r in recs for s in sites]

        prep, push, reply, store = (site(f"flower_relay_{k}_s") for k in ("prep", "push", "reply", "store"))
        train, load, tres = site("train_time_s"), site("data_load_s"), site("t_result_s")
        flower = [a - b - (c or 0) for a, b, c in zip(reply, train, load) if a is not None and b is not None]
        rest = [t - p - q - r - s for t, p, q, r, s in zip(tres, prep, push, reply, store) if None not in (t, p, q, r, s)]
        rows.append({"MB": int(re.search(r"-(\d+)mb-", run.name)[1]),
                     "round_s": med([r.get("round/duration_s") for r in recs]),
                     "fanout_s": med([r.get("adapter/flower/fanout_s") for r in recs]), "t_result_s": med(tres),
                     "prep_s": med(prep), "push_s": med(push), "reply_s": med(reply), "flower_s": med(flower),
                     "train_s": med(train), "store_s": med(store), "rest_s": med(rest),
                     "agg_s": med([r.get("round/agg_s") for r in recs]), "rounds": len(recs)})
    cols = list(rows[0]) if rows else []
    print("| " + " | ".join(cols) + " |")
    print("|" + "---|" * len(cols))
    for r in rows:
        print("| " + " | ".join(str(r[c]) for c in cols) + " |")


if __name__ == "__main__":
    main()

"""E4a NVFlare breakdown from run dirs (metrics.jsonl): medians over rounds >= 2 (round 1 = warm-up).

    python3 -I e4a_breakdown.py <dir with s2-e4a-*-<S>mb-* run dirs> [--fw nvflare]
"""
import json
import re
import statistics as st
import sys
from pathlib import Path


def med(xs):
    xs = [x for x in xs if x is not None]
    return round(st.median(xs), 3) if xs else None


def main():
    root = Path(sys.argv[1])
    fw = sys.argv[sys.argv.index("--fw") + 1] if "--fw" in sys.argv else "nvflare"
    rows = []
    for run in sorted(root.glob("s2-e4a-*mb-*"), key=lambda p: int(re.search(r"-(\d+)mb-", p.name)[1])):
        size = int(re.search(r"-(\d+)mb-", run.name)[1])
        recs = [json.loads(l) for l in (run / "metrics.jsonl").read_text().splitlines() if l.strip()]
        recs = [r for r in recs if r.get("step", 0) >= 2]
        sites = sorted({k.split("/")[1] for r in recs for k in r if k.startswith("site/nvf-")})
        g = lambda key: [r.get(key) for r in recs]  # noqa: E731
        per_site = lambda name: [r.get(f"site/{s}/{name}") for r in recs for s in sites]  # noqa: E731
        reply, train, load = per_site("nvflare_relay_reply_s"), per_site("train_time_s"), per_site("data_load_s")
        transfer = [a - b - (c or 0) for a, b, c in zip(reply, train, load) if a is not None and b is not None]
        tres, prep, store = per_site("t_result_s"), per_site("nvflare_relay_prep_s"), per_site("nvflare_relay_store_s")
        rest = [t - p - r - s for t, p, r, s in zip(tres, prep, reply, store) if None not in (t, p, r, s)]
        rows.append({"MB": size, "round_s": med(g("round/duration_s")), "fanout_s": med(g(f"adapter/{fw}/fanout_s")),
                     "t_result_s": med(tres), "train_s": med(train), "prep_s": med(prep),
                     "nvflare_transfer_s": med(transfer), "store_s": med(store), "rest_s": med(rest),
                     "agg_s": med(g("round/agg_s")), "rounds": len(recs)})
    cols = list(rows[0]) if rows else []
    print("| " + " | ".join(cols) + " |")
    print("|" + "---|" * len(cols))
    for r in rows:
        print("| " + " | ".join(str(r[c]) for c in cols) + " |")


if __name__ == "__main__":
    main()

"""Local-only reference (E1): one site trains alone for N epochs. A module of its own so that
spawned worker processes can import the job function (functions in __main__ cannot be pickled)."""

from __future__ import annotations

import time

from flhub_core.model import init_weights
from flhub_core.weights import sha256_ref, to_safetensors

from hub.orchestrator.executors import load_site_data
from hub.orchestrator.run import make_evaluator


def _local_site(job: tuple) -> tuple:
    cfg, site, spec, split = job
    from flhub_core.train import train

    data = load_site_data(spec, site)
    evaluator = make_evaluator(cfg, split)
    w = init_weights(cfg.seed, cfg.model, cfg.num_classes)
    rows = []
    for epoch in range(1, cfg.rounds + 1):
        t0 = time.monotonic()
        w, n, m = train(w, data, {**cfg.train_config, "round": epoch})
        ev = evaluator(w) if evaluator else {}
        rows.append({"epoch": epoch, "n": n, "train_s": time.monotonic() - t0, "global_ref": sha256_ref(
            to_safetensors(w)), **{k: v for k, v in m.items() if k in ("train_loss", "train_acc")}, **ev})
    return site, rows

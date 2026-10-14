"""Client stub: the hospital-side code a new framework must run (docs/client-contract.md).

A framework runs code like this at every site, once per round, inside its own client pattern:
the NVFlare Client API loop (clients/nvflare/client.py), a Flower ClientApp (clients/flower/),
a vantage6 algorithm function (clients/vantage6/). The framework's part is the wrapper that
receives the message and sends the reply; the flhub part is only what train_round() does.
"""

from __future__ import annotations

import os

import flhub_core
from flhub_core.data import load_site, materialize, site_path
from flhub_core.train import train


def load_data(site_id: str, data_dir: str | None = None):
    """The site's own data. It never leaves the site; only trained weights and metrics do."""
    path = os.environ.get("FLHUB_DATA_PATH") or site_path(site_id, data_dir)
    if not os.path.exists(path):  # benchmark sites only: build the partition from the public dataset
        path = materialize(site_id, data_dir)
    # A lazy handle: train() picks the partition that train_config["data_variant"] names, if any.
    return load_site(path)


def train_round(weights: dict, config: dict, data) -> dict:
    """One round at one site.

    weights: the global model as the hub dispatched it, an ordered {tensor name: float32 array}.
    config:  the hub's train_config plus "round" (and "data_variant" if the hub set one), unchanged.
    Returns what the site's envelope needs; the framework sends it back in its own reply message.
    """
    # The same deterministic training at every site and in the hub's simulation: same weights,
    # data and config give bit-identical weights, whichever framework carried them.
    new_weights, num_examples, metrics = train(weights, data, config)
    return {
        "weights": new_weights,  # same tensor names, shapes and dtype as the global model
        "num_examples": num_examples,  # the site's weight in FedAvg
        "metrics": metrics,  # train_loss, train_acc, timings and data_fp (which data was used)
        "code_version": flhub_core.code_version(),  # the hub can require one version at every site
    }

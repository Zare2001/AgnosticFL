"""Minimal Flower ClientApp for the AgnosticFL hub (example, one copyable file).

This is what a Flower hospital runs so that the hub can train with it: one standard ClientApp
with two handlers. It does the same as clients/flower/flhub_flower/client_app.py and records.py,
in one file, with every essential line explained. [Flower] marks Flower's standard Message-API
pattern, [flhub] what the hub needs; the full contract is docs/client-contract.md.

To use it, put this file into the Flower app that the hub's adapter ships as a FAB and point
`clientapp` in the app's pyproject.toml at it; examples/flower/run_local.py does exactly that.
The hub's adapter adds flhub_core to the same FAB, so the hospital installs only flwr, torch,
numpy and safetensors.
"""

from __future__ import annotations

from collections import OrderedDict

import numpy as np
from flwr.app import Array, ArrayRecord, ConfigRecord, Context, Message, MetricRecord, RecordDict
from flwr.clientapp import ClientApp

from flhub_core import code_version  # [flhub] version + hash of the training code
from flhub_core.data import load_site, site_path  # [flhub] the site's data file
from flhub_core.train import train as flhub_train  # [flhub] the only training code

# [flhub] Record names that the hub's relay ServerApp uses. "arrays" and "config" are also the
# defaults of Flower's own strategies, so this ClientApp works with Flower's FedAvg unchanged.
ARRAYS, CONFIG, METRICS, SITE_INFO = "arrays", "config", "metrics", "site-info"

app = ClientApp()  # [Flower] the object the FAB's pyproject.toml names as clientapp


def site_info(context: Context) -> ConfigRecord:
    """[flhub] Which hub site this SuperNode serves and which training code it runs."""
    return ConfigRecord(
        {
            # set at the hospital: flower-supernode --node-config "site-id='flw-h5' data-path='...'"
            "site-id": str(context.node_config["site-id"]),
            # the hub checks it against its own flhub_core (strict_code_version in the run config)
            "client-code-version": code_version(),
        }
    )


@app.query()
def query(msg: Message, context: Context) -> Message:
    """[flhub] Answer the relay's site-info QUERY.

    [Flower] QUERY is a standard message type. A SuperNode gets a new random node id at every
    start, so the relay asks each new node which hub site it serves.
    """
    return Message(RecordDict({SITE_INFO: site_info(context)}), reply_to=msg)


@app.train()
def train(msg: Message, context: Context) -> Message:
    """[Flower] One TRAIN message: the global weights in, the new weights out."""
    # [Flower] The global weights: an ArrayRecord keyed by the model's state_dict names.
    # [flhub] Keep the names and their order; Array.numpy() gives the exact float32 values.
    weights = OrderedDict((name, arr.numpy()) for name, arr in msg.content[ARRAYS].items())
    # [flhub] The training settings (lr, local_epochs, model, the hub round as "round", an optional
    # data_variant, ...). Pass them on unchanged: the hub hashes them, flhub_core reads them.
    train_config = dict(msg.content[CONFIG])
    # [flhub] The site's data: node config data-path, else $FLHUB_DATA_DIR/<site-id>.csv.
    site_id = str(context.node_config["site-id"])
    data_path = context.node_config.get("data-path") or site_path(site_id)
    # [flhub] Train with the shared library only, never with an own loop: same weights, data and
    # settings give the same result in every framework. load_site() is lazy and reads
    # <variant>-<site>.csv next to the file when train_config has a data_variant.
    new_weights, num_examples, metrics = flhub_train(weights, load_site(data_path), train_config)
    reply = RecordDict(
        {
            # [Flower] the new weights, same names and order, float32
            ARRAYS: ArrayRecord({name: Array(np.ascontiguousarray(a)) for name, a in new_weights.items()}),
            # [flhub] "num-examples" is the FedAvg weight (also Flower FedAvg's default
            # weighted_by_key); the metrics of flhub_core.train include data_fp, which tells the
            # hub which data the site trained on.
            METRICS: MetricRecord({"num-examples": num_examples, **metrics}),
            SITE_INFO: site_info(context),
        }
    )
    return Message(reply, reply_to=msg)  # [Flower] a reply names the message it answers

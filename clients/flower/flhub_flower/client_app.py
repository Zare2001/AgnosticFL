"""Standard Flower ClientApp run by the hospital SuperNodes (flw-h5, flw-h6).

Hospital-side configuration is only the SuperNode's node config:
    --node-config "site-id='flw-h5' data-path='/home/<user>/flhub-data/flw-h5.csv'"
Training itself is flhub_core.train, the same code every framework's clients run.
"""

from __future__ import annotations

from importlib.metadata import version

from flhub_core import code_version
from flhub_core.data import load_site, site_path
from flhub_core.train import train as flhub_train
from flwr.app import ConfigRecord, Context, Message, MetricRecord, RecordDict
from flwr.clientapp import ClientApp

from flhub_flower.records import (
    ARRAYS,
    CONFIG,
    METRICS,
    SITE_INFO,
    arrayrecord_to_weights,
    weights_to_arrayrecord,
)

app = ClientApp()


def _site_info(context: Context) -> ConfigRecord:
    return ConfigRecord(
        {
            "site-id": str(context.node_config["site-id"]),
            "client-code-version": code_version(),
            "flwr-version": version("flwr"),
        }
    )


@app.query()
def query(msg: Message, context: Context) -> Message:
    """Tell the server which hospital site this SuperNode serves."""
    return Message(RecordDict({SITE_INFO: _site_info(context)}), reply_to=msg)


@app.train()
def train(msg: Message, context: Context) -> Message:
    """One local training round from the received global weights."""
    weights = arrayrecord_to_weights(msg.content[ARRAYS])
    train_config = dict(msg.content[CONFIG])  # flhub_core.TrainConfig keys incl. "round"
    site_id = str(context.node_config["site-id"])
    data_path = context.node_config.get("data-path") or site_path(site_id)
    new_weights, num_examples, metrics = flhub_train(weights, load_site(data_path), train_config)
    reply = RecordDict(
        {
            ARRAYS: weights_to_arrayrecord(new_weights),
            METRICS: MetricRecord({"num-examples": num_examples, **metrics}),
            SITE_INFO: _site_info(context),
        }
    )
    return Message(reply, reply_to=msg)

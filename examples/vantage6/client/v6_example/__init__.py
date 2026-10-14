"""Minimal vantage6 algorithm for the flhub hub (example).

A vantage6 node runs this package inside its algorithm container. For every site and round the
vantage6 adapter (adapters/vantage6) creates one task with the input

    {"method": "train",
     "kwargs": {"weights_b64": "<base64 of the global model as safetensors>",
                "train_config": {"local_epochs": 1, "batch_size": 64, ..., "round": 7},
                "sites": {"<organization id>": "<site id>"}}}

and `train` returns the site's update as JSON. Derived from clients/vantage6/v6_flhub/__init__.py,
the client tested in both test stages; what every client must do is in docs/client-contract.md.
"""

from __future__ import annotations

import base64

import flhub_core  # flhub: the shared training library, the same code at every site
from flhub_core import data as fdata
from flhub_core import train as ftrain
from flhub_core import weights as fweights
from vantage6.algorithm.tools.decorators import data, metadata  # vantage6 standard
from vantage6.algorithm.tools.util import info  # vantage6 standard: writes to the run log


@metadata  # vantage6: passes the run's metadata (organization_id, task_id, ...) as `meta`
@data(1)  # vantage6: passes the task's database, read by the node, as a pandas DataFrame `df`
def train(df, meta, weights_b64: str, train_config: dict, sites: dict | None = None) -> dict:
    """One local training round on this node's data (the method named in the task input)."""
    # flhub: the hub knows this site by its site id; the run metadata only has the organisation
    # id, so the adapter sends the map {organisation id: site id} with every task
    site_id = (sites or {}).get(str(int(meta.organization_id)))

    # flhub: the global model arrives inline, as base64 safetensors
    raw = base64.b64decode(weights_b64)

    # flhub: the shared library turns the DataFrame into the site's data and trains exactly one
    # deterministic round with the hub's train_config; no training, preprocessing or randomness of
    # our own. For a data variant the node already passes that variant's database (the adapter
    # chooses it by database label), and train() uses the DataFrame as given.
    site_data = fdata.load_site(df)
    info(f"site={site_id} round={train_config.get('round')} n={len(site_data)}")
    new_weights, num_examples, metrics = ftrain.train(fweights.from_safetensors(raw), site_data, train_config)

    # vantage6: the return value must be plain JSON (wrap_algorithm() writes it with json.dumps).
    # flhub: these are the fields the adapter checks and hands to the hub.
    return {
        "weights_b64": fweights.to_b64(new_weights),  # the update: all tensors, float32, base64 safetensors
        "num_examples": int(num_examples),  # the FedAvg weight
        "metrics": {k: float(v) for k, v in metrics.items()},  # train_loss, ..., data_fp (which data was used)
        "site_id": site_id,  # the adapter rejects a result for another site
        "base_model": fweights.sha256_ref(raw),  # the adapter rejects a result trained from another model
        "client_code_version": flhub_core.code_version(),  # which flhub_core ran (version + content hash)
    }

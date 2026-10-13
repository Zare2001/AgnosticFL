"""AgnosticFL vantage6 algorithm: one local training round with flhub_core.

Standard vantage6 4.15 algorithm: the node runs this image, the wrapper calls the
method named in the task input with the node's database as a pandas DataFrame.

Task input (created by adapters/vantage6):
    {"method": "train",
     "kwargs": {"weights_b64": base64(safetensors of the global model),
                "train_config": {..., "round": r},
                "sites": {"<organization id>": "<site id>"}}}
"""

from __future__ import annotations

import base64

import flhub_core
from flhub_core import data as fdata
from flhub_core import train as ftrain
from flhub_core import weights as fweights
from vantage6.algorithm.tools.decorators import data, metadata
from vantage6.algorithm.tools.util import info


@metadata
@data(1)
def train(df, meta, weights_b64: str, train_config: dict, sites: dict | None = None) -> dict:
    """Train from the global weights on this node's data; return the result as JSON types."""
    org_id = int(meta.organization_id)
    site_id = (sites or {}).get(str(org_id))
    raw = base64.b64decode(weights_b64)
    site_data = fdata.load_site(df)
    info(f"site={site_id} org={org_id} round={train_config.get('round')} n={len(site_data)}")
    new_weights, num_examples, metrics = ftrain.train(fweights.from_safetensors(raw), site_data, train_config)
    return {
        "weights_b64": fweights.to_b64(new_weights),
        "num_examples": int(num_examples),
        "metrics": {k: float(v) for k, v in metrics.items()},
        "site_id": site_id,
        "organization_id": org_id,
        "base_model": fweights.sha256_ref(raw),
        "client_code_version": flhub_core.code_version(),
    }

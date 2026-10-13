"""Message layout shared by the ClientApp and the relay ServerApp.

Canonical weights are an ordered mapping ``name -> float32 ndarray`` (flhub_core
state_dict names). In Flower they travel as an ``ArrayRecord`` keyed by the same
names, so no renaming or reordering happens on the way.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Mapping

import numpy as np
from flwr.app import Array, ArrayRecord

# RecordDict keys
ARRAYS = "arrays"  # ArrayRecord: model weights (request and reply)
CONFIG = "config"  # ConfigRecord: train config + round info (request)
METRICS = "metrics"  # MetricRecord: num-examples, train_loss, ... (reply)
SITE_INFO = "site-info"  # ConfigRecord: site-id, client-code-version (reply)

# QUERY request asking a SuperNode which hospital site it serves
QUERY_SITE_INFO = "site-info"


def weights_to_arrayrecord(weights: Mapping[str, np.ndarray]) -> ArrayRecord:
    """Wrap canonical weights in an ArrayRecord, keeping names and order."""
    return ArrayRecord(
        {name: Array(np.ascontiguousarray(arr)) for name, arr in weights.items()}
    )


def arrayrecord_to_weights(record: ArrayRecord) -> OrderedDict[str, np.ndarray]:
    """Unwrap an ArrayRecord into canonical weights (names and order kept)."""
    return OrderedDict((name, arr.numpy()) for name, arr in record.items())

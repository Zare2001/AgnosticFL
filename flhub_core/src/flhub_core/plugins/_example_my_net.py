"""Example plugin: a model for 3x32x32 images (CIFAR-10). Copy it to a file without the leading "_"
(e.g. my_net.py, in your own folder), change it, and select it in a run configuration with
`model: my-net`. Rules: float32 weights only (GroupNorm or LayerNorm, not BatchNorm); declare
`input_shape`; normalize inputs in forward() (it receives pixel / 255); nothing random or
multi-threaded in forward(), so that every framework computes the same update.
"""

import torch
import torch.nn.functional as F
from torch import nn

from flhub_core.model import register_model


class MyNet(nn.Module):
    """my-net: 2x conv-GroupNorm-pool, then 2 linear layers."""

    input_shape = (3, 32, 32)  # (C, H, W); the run configuration checks it against the dataset

    def __init__(self, num_classes: int = 10, in_channels: int = 3) -> None:
        super().__init__()
        self.conv1 = nn.Conv2d(in_channels, 32, 3, padding=1)
        self.gn1 = nn.GroupNorm(8, 32)
        self.conv2 = nn.Conv2d(32, 64, 3, padding=1)
        self.gn2 = nn.GroupNorm(8, 64)
        self.fc1 = nn.Linear(64 * 8 * 8, 128)
        self.fc2 = nn.Linear(128, num_classes)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = (x - 0.5) / 0.5                                     # pixels 0..1 -> -1..1
        x = F.max_pool2d(F.relu(self.gn1(self.conv1(x))), 2)   # 32 x 16 x 16
        x = F.max_pool2d(F.relu(self.gn2(self.conv2(x))), 2)   # 64 x 8 x 8
        x = F.relu(self.fc1(torch.flatten(x, 1)))
        return self.fc2(x)


register_model("my-net", MyNet)

# A dataset goes into a file of its own (numpy only), for example wards.py:
#
#   from flhub_core.data import DatasetInfo, register_dataset
#   register_dataset(DatasetInfo("wards", "", "", 3, ("low", "medium", "high"), ("own",),
#                                image_shape=(1, 28, 28), splits=()))
#
# url "" = data only the hospitals have: each puts ~/flhub-data/wards-own-<site>.csv, and the run
# configuration sets dataset: wards, partition: own, eval: {every: 0}.

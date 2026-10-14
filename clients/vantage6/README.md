# vantage6 hospital client

The vantage6 algorithm that runs at the hospitals: package `v6_flhub` with one method, `train`, which runs one local round with `flhub_core.train` on the node's database. The hospitals run standard vantage6 4.15 nodes, which pull this image from the operator's registry for each task.

| File | What |
|---|---|
| `v6_flhub/__init__.py`, `pyproject.toml` | the algorithm package (`@data(1)`, `@metadata`, JSON result) |
| `Dockerfile`, `Dockerfile.dockerignore` | the image: Python 3.11, `vantage6-algorithm-tools` 4.15.2, pinned torch, numpy and safetensors, `flhub_core`, and `wrap_algorithm()` as entry point |

Build from the repository root (pins in `deploy/versions.env`):

    docker build -f clients/vantage6/Dockerfile --build-arg TORCH_VERSION=2.14.1 --build-arg NUMPY_VERSION=2.4.4 \
      --build-arg SAFETENSORS_VERSION=0.8.0 --build-arg VANTAGE6_VERSION=4.15.2 -t <registry-host>:5050/flhub/v6-train:<tag> .

What the client must contain and how new versions reach the hospitals: [the vantage6 guide](../../docs/frameworks/vantage6.md), §3.

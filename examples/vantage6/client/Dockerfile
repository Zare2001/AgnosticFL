# Algorithm image of the vantage6 example (build context = the repository root):
#   docker build -f examples/vantage6/client/Dockerfile \
#     --build-arg TORCH_VERSION=2.14.1 --build-arg NUMPY_VERSION=2.4.4 \
#     --build-arg SAFETENSORS_VERSION=0.8.0 --build-arg VANTAGE6_VERSION=4.15.2 \
#     -t <registry-host>:5050/flhub/v6-example:<tag> .
# The steps are those of clients/vantage6/Dockerfile, the image used in both test stages; only the
# package differs. vantage6's own algorithm-base:4.15 image is Python 3.10; this one is Python 3.11,
# so that torch and numpy are the same versions as at every other site (bit-identical training).
ARG BASE_IMAGE=python:3.11.17-slim-bookworm
FROM ${BASE_IMAGE}
ARG TORCH_VERSION
ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu
ARG NUMPY_VERSION
ARG SAFETENSORS_VERSION
ARG VANTAGE6_VERSION
ARG PANDAS_VERSION=2.3.3
# the pinned numeric stack (deploy/versions.env) and the vantage6 algorithm tools
RUN pip install --no-cache-dir "torch==${TORCH_VERSION}+cpu" --index-url "${TORCH_INDEX_URL}" \
 && pip install --no-cache-dir "numpy==${NUMPY_VERSION}" "safetensors==${SAFETENSORS_VERSION}" \
      "pandas==${PANDAS_VERSION}" "vantage6-algorithm-tools==${VANTAGE6_VERSION}"
# flhub: the shared training library and the algorithm package (Dockerfile.dockerignore keeps the
# build context to these two)
COPY flhub_core /src/flhub_core
COPY examples/vantage6/client /src/v6_example
RUN pip install --no-cache-dir --no-deps /src/flhub_core /src/v6_example && rm -rf /src
# vantage6 standard: wrap_algorithm() reads the task input and calls its method in package PKG_NAME
ENV PKG_NAME=v6_example
CMD ["python", "-c", "from vantage6.algorithm.tools.wrap import wrap_algorithm; wrap_algorithm()"]

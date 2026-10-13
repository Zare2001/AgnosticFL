# AgnosticFL vantage6 algorithm image (build context = repo root):
#   docker build -f clients/vantage6/Dockerfile --build-arg TORCH_VERSION=... -t <registry>/flhub/v6-train:<tag> .
# Same as the official algorithm-base:4.15 (python slim + vantage6-algorithm-tools) but on
# Python 3.11 with the project's torch/numpy pins (docs/DECISIONS.md ADR-004).
ARG BASE_IMAGE=python:3.11.17-slim-bookworm
FROM ${BASE_IMAGE}
ARG TORCH_VERSION
ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu
ARG NUMPY_VERSION
ARG SAFETENSORS_VERSION
ARG VANTAGE6_VERSION
ARG PANDAS_VERSION=2.3.3
RUN pip install --no-cache-dir "torch==${TORCH_VERSION}+cpu" --index-url "${TORCH_INDEX_URL}" \
 && pip install --no-cache-dir "numpy==${NUMPY_VERSION}" "safetensors==${SAFETENSORS_VERSION}" \
      "pandas==${PANDAS_VERSION}" "vantage6-algorithm-tools==${VANTAGE6_VERSION}"
COPY flhub_core /src/flhub_core
COPY clients/vantage6 /src/v6_flhub
RUN pip install --no-cache-dir --no-deps /src/flhub_core /src/v6_flhub && rm -rf /src
ENV PKG_NAME=v6_flhub
CMD ["python", "-c", "from vantage6.algorithm.tools.wrap import wrap_algorithm; wrap_algorithm()"]

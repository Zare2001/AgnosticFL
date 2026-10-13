# E7 native baseline image = the hub's algorithm image (same v6_flhub.train, same flhub_core) plus
# the central function v6_native.fedavg. Tagged <hub tag>-native in the same repository, so the
# hospitals' image policy (flhub/v6-train:<tag>) admits it without a node change.
#   docker build --build-arg BASE=<registry>/flhub/v6-train:<tag> -t <registry>/flhub/v6-train:<tag>-native deploy/central/vantage6/native
ARG BASE
FROM ${BASE}
COPY v6_native /opt/v6-native/v6_native
ENV PYTHONPATH=/opt/v6-native PKG_NAME=v6_native

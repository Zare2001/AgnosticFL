#!/usr/bin/env bash
# Demo data only. Writes this site's share of a public dataset to $DATA_DIR as <variant>-<site>.csv,
# checked against the operator's partition manifests in manifests/:
#   ./prepare_data.sh                  # the variants of kit.env (DEMO_VARIANTS, e.g. cifar10-iid)
#   ./prepare_data.sh cifar10-dir0.3   # another variant
# A real hospital puts its own file there instead: one CSV per variant, header label,x0..x3071
# (3x32x32 images, channel-first R, G, B planes, integer pixels 0-255) or label,x0..x783 (1x28x28).
# The data tool is a copy of the operator's flhub_core, used here with numpy only; it is not
# installed into the client.
. "$(dirname "$0")/lib.sh"
variants=("$@")
[ ${#variants[@]} -gt 0 ] || read -ra variants <<<"$DEMO_VARIANTS"
[ ${#variants[@]} -gt 0 ] || die "no data variant given (./prepare_data.sh cifar10-iid)"
ensure_uv
tool="$KIT/data-venv"
[ -x "$tool/bin/python" ] || uv venv -q --python "$PYTHON_VERSION" "$tool"
uv pip install -q --python "$tool/bin/python" "numpy==$NUMPY_VERSION"
mkdir -p "$DATA_DIR"
for v in "${variants[@]}"; do
  verify=()
  if [ -f "$KIT/manifests/$v.json" ]; then
    verify=(--manifests "$KIT/manifests")
  else
    log "warning: the kit has no manifest for $v; the file is written without the sha256 check"
  fi
  log "data: $v (downloads the public dataset once into $DATA_DIR/raw)"
  PYTHONPATH="$KIT/data-tool" FLHUB_DATA_DIR="$DATA_DIR" "$tool/bin/python" -m flhub_core.data materialize \
    --variants "$v" --sites "$SITE" --out "$DATA_DIR" "${verify[@]}" | sed "s/^/[$SITE] data: /"
done
log "data: OK (vantage6: ./stop.sh && ./start.sh makes the node offer new files)"

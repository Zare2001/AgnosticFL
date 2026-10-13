#!/usr/bin/env bash
# E5: startup kits for the extra NVFlare sites ($NVF_EXTRA_SITES, default nvf-h7 nvf-h8), signed by the
# EXISTING root CA: `nvflare provision --add-client` reloads <ws>/<project>/state/cert.json, so the
# running server and the base kits stay valid (no server restart; the server has no client allow-list).
# Each run writes the next prod_NN; the site kit is packed as $NVF_KITS_OUT/<site>.tgz for
#   tools/bus put $NVF_KITS_OUT/<site>.tgz --to nvflare --secret
# Idempotent: a site that already has a kit in some prod_NN is only re-packed.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/common.sh"
state="$NVF_WS/$NVF_PROJECT/state/cert.json"
[ -f "$state" ] || { echo "provision_extra: no provisioning state in $state; run provision.sh first" >&2; exit 1; }
[ -n "$NVF_EXTRA_SITES" ] || { echo "provision_extra: NVF_EXTRA_SITES is empty" >&2; exit 1; }
kit_of() { ls -d "$NVF_WS/$NVF_PROJECT"/prod_*/"$1"/startup 2>/dev/null | sort -V | tail -1 | xargs -r dirname; }
mkdir -p "$NVF_KITS_OUT" && chmod 700 "$NVF_KITS_OUT"
for site in $NVF_EXTRA_SITES; do
  if [ -z "$(kit_of "$site")" ]; then
    num=${site##*[!0-9]}
    printf 'name: %s\norg: hospital%s\n' "$site" "$num" > "$NVF_RUNTIME/add-$site.yml"
    "$NVF_VENV/bin/nvflare" provision -p "$NVF_RUNTIME/project.yml" -w "$NVF_WS" \
      --add-client "$NVF_RUNTIME/add-$site.yml" --force > "$NVF_RUNTIME/provision-$site.log" 2>&1 \
      || { tail -20 "$NVF_RUNTIME/provision-$site.log"; exit 1; }
  fi
  kit=$(kit_of "$site")
  [ -n "$kit" ] || { echo "provision_extra: no kit for $site after provisioning" >&2; exit 1; }
  ca_new=$(sha256sum < "$kit/startup/rootCA.pem"); ca_srv=$(sha256sum < "$NVF_SERVER_KIT/startup/rootCA.pem")
  [ "$ca_new" = "$ca_srv" ] || { echo "provision_extra: $site kit has a different root CA" >&2; exit 1; }
  tar -czf "$NVF_KITS_OUT/$site.tgz" -C "$(dirname "$kit")" "$site"
  chmod 600 "$NVF_KITS_OUT/$site.tgz"
  echo "provision_extra: $site kit from $(basename "$(dirname "$kit")") (same root CA) -> $NVF_KITS_OUT/$site.tgz"
done

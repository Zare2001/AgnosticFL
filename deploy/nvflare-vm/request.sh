#!/usr/bin/env bash
# Hospital: makes this site's NVFlare private key and certificate request (distributed provisioning).
#   bash deploy/nvflare-vm/request.sh <site> [--force]
# The key stays in $NVF_REQ_DIR/<site>/ (mode 700) and later moves into the site's kit; it never
# leaves this machine. Send the operator only <site>.request.zip (CSR + metadata, no key), and the
# public key hash this script prints, in the same bus message:
#   tools/bus put $NVF_REQ_DIR/<site>/<site>.request.zip --to central
#   tools/bus send --to central --type request --subject "nvflare cert request <site>" --body "pubkey sha256 <hash>"
# The operator answers with <site>.signed.zip: put it in $NVF_INCOMING and run install.sh.
# --force replaces an existing request (a new key); without it an existing request is kept.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/common.sh"
site="${1:-}"; force="${2:-}"
[ -n "$site" ] || { sed -n '2,10p' "$0" >&2; exit 2; }
grep -qw -- "$site" <<<"$NVF_SITES $NVF_EXTRA_SITES" || { echo "request: unknown site $site (expected one of: $NVF_SITES $NVF_EXTRA_SITES)" >&2; exit 1; }
num=$(sed -n 's/^nvf-h\([0-9][0-9]*\).*/\1/p' <<<"$site")
[ -n "$num" ] || { echo "request: site name $site is not nvf-h<N>" >&2; exit 1; }
req="$NVF_REQ_DIR/$site"
zip="$req/$site.request.zip"

mkdir -p "$NVF_REQ_DIR" && chmod 700 "$NVF_REQ_DIR"
if [ -f "$zip" ] && [ -f "$req/$site.key" ] && [ "$force" != --force ]; then
  echo "request: kept the existing request of $site (--force for a new key)"
else
  rm -rf "$req" && mkdir -p "$req" && chmod 700 "$req"
  printf 'name: %s\nparticipants:\n  - name: %s\n    type: client\n    org: hospital%s\n' \
    "$NVF_PROJECT" "$site" "$num" > "$req/participant.yaml"
  "$NVF_VENV/bin/nvflare" cert request -p "$req/participant.yaml" --out "$req" --force > "$req/request.log" 2>&1 \
    || { tail -10 "$req/request.log" >&2; exit 1; }
  chmod 600 "$req/$site.key"
fi

# the zip must not carry the key; the hash is computed from the CSR inside the zip itself
pub=$("$NVF_VENV/bin/python" - "$zip" "$site" <<'PY'
import hashlib, sys, zipfile
from cryptography import x509
from cryptography.hazmat.primitives import serialization
zf = zipfile.ZipFile(sys.argv[1])
for i in zf.infolist():
    if i.filename.endswith(".key") or b"PRIVATE KEY" in zf.read(i):
        sys.exit(f"request: {i.filename} in the zip holds a private key; not sending it")
csr = x509.load_pem_x509_csr(zf.read(f"{sys.argv[2]}.csr"))
der = csr.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
print(hashlib.sha256(der).hexdigest())
PY
)
echo "request: $site (project $NVF_PROJECT, org hospital$num): $zip"
echo "request: pubkey sha256 $pub"
echo "request: send with: tools/bus put $zip --to central"
echo "request:   and say in the bus message: nvflare cert request $site pubkey sha256 $pub"

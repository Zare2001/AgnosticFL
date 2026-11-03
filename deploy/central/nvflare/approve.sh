#!/usr/bin/env bash
# Central: signs one hospital's NVFlare certificate request (distributed provisioning).
#   bash deploy/central/nvflare/approve.sh <site>.request.zip --expect-pubkey <sha256> [--reissue]
# <sha256> is the public key hash the hospital printed with request.sh and sent in its bus message;
# approval is refused unless the CSR in the zip has exactly that key. Also refused: a zip with any
# private key in it, another project, a type other than client, a name not in $NVF_SITES, an org
# other than hospital<N>, and a site that already has a valid certificate (unless --reissue).
# Output: $NVF_SIGNED_OUT/<site>.signed.zip (certificate + rootCA.pem, no secret) to return with
#   tools/bus put $NVF_SIGNED_OUT/<site>.signed.zip --to nvflare
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/common.sh"
usage() { sed -n '2,9p' "$0" >&2; exit 2; }
zip="" expect="" reissue=0
while [ $# -gt 0 ]; do
  case "$1" in
    --expect-pubkey) [ $# -ge 2 ] || usage; expect=$2; shift 2 ;;
    --reissue) reissue=1; shift ;;
    -h|--help) usage ;;
    -*) echo "approve: unknown option $1" >&2; usage ;;
    *) [ -z "$zip" ] || usage; zip=$1; shift ;;
  esac
done
[ -n "$zip" ] && [ -f "$zip" ] || { echo "approve: no request zip given" >&2; usage; }
[[ "$expect" =~ ^[0-9a-f]{64}$ ]] || { echo "approve: --expect-pubkey <64 hex chars> is required" >&2; exit 2; }
[ -f "$NVF_CA_DIR/ca.json" ] && [ -f "$NVF_PROFILE" ] || { echo "approve: no CA; run provision.sh first" >&2; exit 1; }

# One private copy is checked AND signed (no swap between the check and `nvflare cert approve`).
work=$(mktemp -d); chmod 700 "$work"; trap 'rm -rf "$work"' EXIT
cp -- "$zip" "$work/request.zip"; zip="$work/request.zip"

# Checks the zip and prints the site name; exits non-zero with the reason otherwise.
site=$("$NVF_VENV/bin/python" - "$zip" "$expect" "$NVF_PROJECT" "$NVF_SITES" <<'PY'
import hashlib, json, re, sys, zipfile
from cryptography import x509
from cryptography.hazmat.primitives import serialization

path, expect, project, allowed = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4].split()
def fail(msg):
    print(f"approve: REFUSED: {msg}", file=sys.stderr); sys.exit(1)
with zipfile.ZipFile(path) as zf:
    members = {i.filename: zf.read(i) for i in zf.infolist() if not i.is_dir()}
for n, b in members.items():
    if n.endswith(".key") or b"PRIVATE KEY" in b:
        fail(f"the zip holds private key material ({n}); the key must stay at the hospital")
if set(members) - {"request.json", "site.yaml"} - {n for n in members if n.endswith(".csr")}:
    fail(f"unexpected files in the zip: {sorted(members)}")
meta = json.loads(members["request.json"])
name = meta.get("name", "")
csrs = [n for n in members if n.endswith(".csr")]
if csrs != [f"{name}.csr"]:
    fail(f"expected exactly {name}.csr, found {csrs}")
if meta.get("project") != project:
    fail(f"project {meta.get('project')!r}, expected {project!r}")
if meta.get("cert_type") != "client" or meta.get("kind") != "site":
    fail(f"type {meta.get('kind')}/{meta.get('cert_type')}, only hospital clients (site/client) are approved here")
if name not in allowed:
    fail(f"site {name!r} is not one of {allowed}")
m = re.match(r"^nvf-h(\d+)", name)
if not m or meta.get("org") != f"hospital{m.group(1)}":
    fail(f"org {meta.get('org')!r} for {name}, expected hospital<N> of nvf-h<N>")
csr = x509.load_pem_x509_csr(members[f"{name}.csr"])
if not csr.is_signature_valid:
    fail("the CSR signature is invalid")
cn = csr.subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME)
if not cn or cn[0].value != name:
    fail(f"CSR common name {cn[0].value if cn else None!r} is not {name!r}")
der = csr.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
pub = hashlib.sha256(der).hexdigest()
print(f"approve: {name}: CSR public key sha256 {pub}", file=sys.stderr)
if meta.get("public_key_sha256") != pub:
    fail("request.json public_key_sha256 does not match the CSR")
if pub != expect:
    fail(f"public key {pub} is not the one the hospital announced ({expect})")
print(name)
PY
)

issued="$NVF_ISSUED/$site.crt"
ilog="$NVF_ISSUED/issued.log"   # append-only: <utc> <site> <serial> <notAfter epoch> <pubkey sha256>
valid_logged=$(awk -v s="$site" -v n="$(date +%s)" '$2 == s && $4 > n' "$ilog" 2>/dev/null | tail -1 || true)
if [ -n "$valid_logged" ] || { [ -f "$issued" ] && openssl x509 -in "$issued" -noout -checkend 0 >/dev/null 2>&1 \
   && openssl verify -CAfile "$NVF_CA_DIR/rootCA.pem" "$issued" >/dev/null 2>&1; }; then
  if [ "$reissue" = 0 ]; then
    echo "approve: REFUSED: $site already has a valid certificate (${valid_logged:-$(openssl x509 -in "$issued" -noout -enddate)}); use --reissue" >&2
    exit 1
  fi
  echo "approve: --reissue: new certificate for $site; the old one stays valid until it expires (NVFlare has no revocation)"
fi

out="$NVF_SIGNED_OUT/$site.signed.zip"
"$NVF_VENV/bin/nvflare" cert approve "$zip" -c "$NVF_CA_DIR" --profile "$NVF_PROFILE" --out "$out" --force \
  > "$NVF_SIGNED_OUT/$site.approve.log" 2>&1 || { tail -20 "$NVF_SIGNED_OUT/$site.approve.log" >&2; exit 1; }
chmod 644 "$out"
"$NVF_VENV/bin/python" -c 'import sys, zipfile; open(sys.argv[2], "wb").write(zipfile.ZipFile(sys.argv[1]).read(sys.argv[3]))' \
  "$out" "$issued.new" "$site.crt"
openssl verify -CAfile "$NVF_CA_DIR/rootCA.pem" "$issued.new" >/dev/null \
  || { echo "approve: the signed certificate does not verify against the root CA" >&2; rm -f "$out" "$issued.new"; exit 1; }
# what was signed is what was checked: the hospital's announced key, its name and its org
"$NVF_VENV/bin/python" - "$issued.new" "$expect" "$site" <<'PY' || { rm -f "$out" "$issued.new"; exit 1; }
import hashlib, re, sys
from cryptography import x509
from cryptography.hazmat.primitives import serialization
c = x509.load_pem_x509_certificate(open(sys.argv[1], "rb").read())
der = c.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
cn = [a.value for a in c.subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME)]
o = [a.value for a in c.subject.get_attributes_for_oid(x509.NameOID.ORGANIZATION_NAME)]
want_o = "hospital" + re.match(r"^nvf-h(\d+)", sys.argv[3]).group(1)
if hashlib.sha256(der).hexdigest() != sys.argv[2] or cn != [sys.argv[3]] or o != [want_o]:
    sys.exit(f"approve: the signed certificate does not match the request (CN {cn}, O {o}); not returned")
PY
mv "$issued.new" "$issued"
printf '%s %s %s %s %s\n' "$(date -u +%FT%TZ)" "$site" "$(openssl x509 -in "$issued" -noout -serial | cut -d= -f2)" \
  "$(date -d "$(openssl x509 -in "$issued" -noout -enddate | cut -d= -f2)" +%s)" "$expect" >> "$ilog"
echo "approve: signed $site ($(openssl x509 -in "$issued" -noout -serial -enddate | tr '\n' ' '))"
echo "approve: root CA fingerprint $(nvf_ca_fingerprint)"
echo "approve: return it with: tools/bus put $out --to nvflare"

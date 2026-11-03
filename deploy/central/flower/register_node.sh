#!/usr/bin/env bash
# Register, list or remove hospital SuperNodes on the central SuperLink (FLOWER_TLS=1).
# A hospital makes its own P-384 key pair (deploy/flower-vm/keygen.sh) and sends only <site>.pub
# with its sha256; the private key never leaves the hospital and no key is made here.
#   bash deploy/central/flower/register_node.sh add <site> <site.pub> --expect-sha256 <hex> [--replace]
#   bash deploy/central/flower/register_node.sh remove <site>
#   bash deploy/central/flower/register_node.sh list
# add: refuses a site that is not a Flower site of this deployment ($SITES_FLOWER), a key file whose
# sha256 (sha256sum of the .pub file as sent) differs from --expect-sha256, and a key that is not
# ecdsa-sha2-nistp384. A site that is already registered needs --replace (key rotation: the new key
# is registered first, then the old node is unregistered). A key that was once unregistered can never
# be registered again (Flower keeps it, UNIQUE public_key): a hospital that rotates makes a new key.
# The SuperLink gives the key a node id; $NODES_MAP records
# "site node_id pubkey_sha256" and the relay accepts a node for a site only with that node id.
set -euo pipefail
. "$(dirname "$0")/common.sh"
export FLWR_HOME="$FLWR_HOME_DIR"
die() { echo "register_node: $*" >&2; exit 1; }
map_get() { [ -f "$NODES_MAP" ] && awk -v s="$1" '$1 == s {print $2}' "$NODES_MAP" | tail -1; return 0; }
map_write() {  # map_write <site> [<line>]: the map without site's line, plus <line>; one atomic rename
  local tmp; tmp="$(mktemp "$NODES_MAP.XXXX")"
  if [ -f "$NODES_MAP" ]; then awk -v s="$1" '$1 != s' "$NODES_MAP" > "$tmp"; fi
  if [ -n "${2:-}" ]; then echo "$2" >> "$tmp"; fi
  chmod 644 "$tmp"; mv "$tmp" "$NODES_MAP"
}
valid_site() {  # exact Flower site of this deployment, safe as a map field and a file name
  local s
  [[ "$1" =~ ^[a-z0-9][a-z0-9-]{0,62}$ ]] || return 1
  for s in $SITES_FLOWER; do [ "$s" = "$1" ] && return 0; done
  return 1
}
exec 9> "$RUNTIME/register_node.lock"
flock -w 60 9 || die "another register_node.sh is running"
flwr_unregister() { flwr supernode unregister "$1" flhub >/dev/null || die "flwr supernode unregister $1 failed"; }

cmd="${1:-}"; shift || true
case "$cmd" in
  add)
    site="${1:-}"; pub="${2:-}"; shift 2 || die "usage: add <site> <site.pub> --expect-sha256 <hex> [--replace]"
    expect=""; replace=0
    while [ $# -gt 0 ]; do
      case "$1" in
        --expect-sha256) expect="${2:-}"; shift 2 ;;
        --replace) replace=1; shift ;;
        *) die "unknown option $1" ;;
      esac
    done
    valid_site "$site" || die "'$site' is not a Flower site here ($SITES_FLOWER)"
    [ -f "$pub" ] || die "no key file $pub"
    [ -n "$expect" ] || die "--expect-sha256 is required (the sha256 the hospital sent with its key)"
    got="$(sha256sum "$pub" | cut -d' ' -f1)"
    [ "$got" = "${expect,,}" ] || die "$pub sha256 $got != expected $expect: refused"
    [ "$(wc -l < "$pub")" -le 1 ] && head -c 20 "$pub" | grep -q '^ecdsa-sha2-nistp384 ' \
        || die "$pub is not one ecdsa-sha2-nistp384 public key (ssh-keygen -t ecdsa -b 384)"
    old="$(map_get "$site")"
    [ -z "$old" ] || [ "$replace" = 1 ] || die "$site is registered (node $old); use --replace to rotate its key"
    out="$(flwr supernode register "$pub" flhub --name "$site" --format json)" \
        || die "flwr supernode register failed (a key that was registered before cannot be reused): $out"
    # flwr exits 0 also on failure (e.g. "[code: 23] Public key already in use"): read the JSON
    nid="$(printf '%s' "$out" | "$VENV/bin/python" -c '
import json, sys
r = json.load(sys.stdin)
if not r.get("success", True) or "node-id" not in r:
    sys.exit(" ".join(str(r.get("error-message", r)).split()))
nid = int(r["node-id"])
if nid <= 0:  # Flower answers 0 when it could not create the node
    sys.exit(f"SuperLink returned node id {nid}")
print(nid)')" || die "$site not registered (a key that was registered before cannot be reused)"
    map_write "$site" "$site $nid $got"
    echo "register_node: $site registered as node $nid (key sha256 $got)"
    if [ -n "$old" ] && [ "$old" != "$nid" ]; then
      flwr_unregister "$old"
      echo "register_node: $site: old node $old unregistered (its key is refused from now on)"
    fi ;;
  remove)
    site="${1:-}"; valid_site "$site" || die "usage: remove <site> (a Flower site of this deployment)"
    old="$(map_get "$site")"; [ -n "$old" ] || die "$site is not registered"
    flwr_unregister "$old"; map_write "$site"
    echo "register_node: $site (node $old) unregistered" ;;
  list)
    echo "# $NODES_MAP (site node_id pubkey_sha256)"; [ -f "$NODES_MAP" ] && cat "$NODES_MAP"
    flwr supernode list flhub ;;
  *) sed -n '2,12p' "$0"; exit 2 ;;
esac

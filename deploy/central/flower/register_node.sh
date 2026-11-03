#!/usr/bin/env bash
# Register, list or remove hospital SuperNodes on the central SuperLink (FLOWER_TLS=1).
# A hospital makes its own P-384 key pair (deploy/flower-vm/keygen.sh) and sends only <site>.pub
# with its sha256; the private key never leaves the hospital and no key is made here.
#   bash deploy/central/flower/register_node.sh add <site> <site.pub> --expect-sha256 <hex> [--replace]
#   bash deploy/central/flower/register_node.sh remove <site>
#   bash deploy/central/flower/register_node.sh list
# add: refuses a site that is not a Flower site of this deployment ($SITES_FLOWER), a key file whose
# sha256 (sha256sum of the .pub file as sent) differs from --expect-sha256, and a key that is not
# ecdsa-sha2-nistp384. A site that is already registered needs --replace (key rotation: the old
# node is unregistered first). The SuperLink gives the key a node id; $NODES_MAP records
# "site node_id pubkey_sha256" and the relay accepts a node for a site only with that node id.
set -euo pipefail
. "$(dirname "$0")/common.sh"
export FLWR_HOME="$FLWR_HOME_DIR"
die() { echo "register_node: $*" >&2; exit 1; }
map_get() { [ -f "$NODES_MAP" ] && awk -v s="$1" '$1 == s {print $2}' "$NODES_MAP" | tail -1; return 0; }
map_drop() {  # rewrite the map without site $1 (atomic rename: the relay re-reads it)
  local tmp; tmp="$(mktemp "$NODES_MAP.XXXX")"
  [ -f "$NODES_MAP" ] && awk -v s="$1" '$1 != s' "$NODES_MAP" > "$tmp"
  chmod 644 "$tmp"; mv "$tmp" "$NODES_MAP"
}
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
    case " $SITES_FLOWER " in *" $site "*) ;; *) die "$site is not a Flower site here ($SITES_FLOWER)" ;; esac
    [ -f "$pub" ] || die "no key file $pub"
    [ -n "$expect" ] || die "--expect-sha256 is required (the sha256 the hospital sent with its key)"
    got="$(sha256sum "$pub" | cut -d' ' -f1)"
    [ "$got" = "${expect,,}" ] || die "$pub sha256 $got != expected $expect: refused"
    [ "$(wc -l < "$pub")" -le 1 ] && head -c 20 "$pub" | grep -q '^ecdsa-sha2-nistp384 ' \
        || die "$pub is not one ecdsa-sha2-nistp384 public key (ssh-keygen -t ecdsa -b 384)"
    old="$(map_get "$site")"
    if [ -n "$old" ]; then
      [ "$replace" = 1 ] || die "$site is registered (node $old); use --replace to rotate its key"
      flwr_unregister "$old"; map_drop "$site"
      echo "register_node: $site: old node $old unregistered"
    fi
    out="$(flwr supernode register "$pub" flhub --name "$site" --format json)" \
        || die "flwr supernode register failed: $out"
    nid="$(printf '%s' "$out" | "$VENV/bin/python" -c 'import json,sys; print(json.load(sys.stdin)["node-id"])')" \
        || die "no node id in: $out"
    map_drop "$site"
    echo "$site $nid $got" >> "$NODES_MAP"
    echo "register_node: $site registered as node $nid (key sha256 $got)" ;;
  remove)
    site="${1:-}"; [ -n "$site" ] || die "usage: remove <site>"
    old="$(map_get "$site")"; [ -n "$old" ] || die "$site is not registered"
    flwr_unregister "$old"; map_drop "$site"
    echo "register_node: $site (node $old) unregistered" ;;
  list)
    echo "# $NODES_MAP (site node_id pubkey_sha256)"; [ -f "$NODES_MAP" ] && cat "$NODES_MAP"
    flwr supernode list flhub ;;
  *) sed -n '2,12p' "$0"; exit 2 ;;
esac

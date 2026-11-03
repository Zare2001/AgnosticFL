#!/usr/bin/env bash
# Hospital side: make this site's SuperNode key pair (P-384), here, once. The private key
# $KEY_DIR/<site> never leaves this machine; send only <site>.pub and its sha256 to central,
# which registers it (deploy/central/flower/register_node.sh add ... --expect-sha256 <sha256>).
#   bash deploy/flower-vm/keygen.sh <site> [--send]    # --send: tools/bus put <site>.pub --to central
set -euo pipefail
. "$(dirname "$0")/common.sh"
site="${1:?usage: keygen.sh <site> [--send]}"
[[ "$site" =~ ^[a-z0-9][a-z0-9-]{0,62}$ ]] || { echo "keygen: bad site id '$site'" >&2; exit 2; }
umask 077
mkdir -p "$KEY_DIR" && chmod 700 "$KEY_DIR"
if [ -f "$KEY_DIR/$site" ]; then
  echo "keygen: $KEY_DIR/$site exists, kept (delete it and re-register at central to rotate)"
else
  ssh-keygen -q -t ecdsa -b 384 -N "" -C "flhub-$site" -f "$KEY_DIR/$site"
  echo "keygen: new key pair $KEY_DIR/$site (private, stays here) + $site.pub"
fi
chmod 600 "$KEY_DIR/$site"; chmod 644 "$KEY_DIR/$site.pub"
sha="$(sha256sum "$KEY_DIR/$site.pub" | cut -d' ' -f1)"
echo "public key: $KEY_DIR/$site.pub  sha256 $sha"
if [ "${2:-}" = "--send" ]; then
  (cd "$REPO" && tools/bus put "$KEY_DIR/$site.pub" --to central)
  echo "sent $site.pub to central; put its sha256 $sha in the bus message to central"
fi

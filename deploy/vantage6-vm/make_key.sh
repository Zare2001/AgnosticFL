#!/usr/bin/env bash
# The HOSPITAL makes its own vantage6 node API key (vantage6 VM). The key is a random UUID4
# (vantage6 4.15.2 node keys must be UUIDs); it is written to ~/flhub-runtime/vantage6/keys/<site>.key
# (test collaboration: keys/<site>@<collab>.key) (mode 600) on this VM and sent once, encrypted, to central (tools/bus put --secret), which
# registers it with deploy/central/vantage6/register_node.sh. Central never generates node keys.
# install.sh / extra.sh / keytest.sh render the node config from this file.
# Usage: bash deploy/vantage6-vm/make_key.sh SITE [--rotate] [--no-send] [--collab NAME]
#   --rotate   replace an existing key (the old one is kept as <site>.key.old until install.sh
#              has rendered the node config with the new key and the node is connected again)
#   --no-send  only make the key; send it later with --send-only
#   --send-only  send the existing key (e.g. after --no-send)
#   --collab   the collaboration central registers it in (default flhub; test: flhub-keytest,
#              used by keytest.sh)
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
KEYS_DIR="$HOME/flhub-runtime/vantage6/keys"
SITE="${1:?usage: make_key.sh SITE [--rotate] [--no-send|--send-only] [--collab NAME]}"; shift
ROTATE=0; SEND=1; MAKE=1; COLLAB=flhub
while [ $# -gt 0 ]; do
  case "$1" in
    --rotate) ROTATE=1 ;; --no-send) SEND=0 ;; --send-only) MAKE=0 ;;
    --collab) COLLAB="$2"; shift ;;
    *) echo "unknown option $1" >&2; exit 2 ;;
  esac
  shift
done
[[ "$SITE" =~ ^v6-h[0-9]+$ ]] || { echo "site name $SITE: expected v6-h<N>" >&2; exit 2; }
log() { echo "[v6-make-key] $*"; }
mkdir -p "$KEYS_DIR"; chmod 700 "$HOME/flhub-runtime" "$HOME/flhub-runtime/vantage6" "$KEYS_DIR"
KEY="$KEYS_DIR/$SITE$([ "$COLLAB" = flhub ] || echo "@$COLLAB").key"

if [ "$MAKE" = 1 ]; then
  if [ -f "$KEY" ]; then
    [ "$ROTATE" = 1 ] || { echo "$KEY exists; use --rotate to replace it" >&2; exit 1; }
    mv "$KEY" "$KEY.old"; log "old key kept as $KEY.old"
  fi
  (umask 077; python3 -c 'import uuid; print(uuid.uuid4())' > "$KEY")
  chmod 600 "$KEY"
  log "new key $KEY"
fi
[ -f "$KEY" ] || { echo "no key $KEY" >&2; exit 1; }
FP="$(tr -d '[:space:]' < "$KEY" | sha256sum | cut -c1-12)"
log "key fingerprint fp=$FP"

if [ "$SEND" = 1 ]; then
  out="$(cd "$REPO" && tools/bus put "$KEY" --to central --secret --note "v6 node key $SITE fp=$FP collab=$COLLAB")"
  echo "$out" | sed 's/^/[v6-make-key] /'
  log "central registers it: tools/bus get <id> -o <file>; bash deploy/central/vantage6/register_node.sh $SITE <file>$([ "$COLLAB" = flhub ] || echo " --collab $COLLAB") --fingerprint $FP"
fi

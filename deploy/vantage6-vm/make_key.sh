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
#   --confirm FP  after central reports "registered ... fp=FP": marks the key registered
#              (<key>.registered). install.sh/extra.sh use a key only when it is marked, so a key
#              that central has not registered yet never replaces the working one. Also removes
#              <key>.old (the rotated-away key, now refused by the server).
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
KEYS_DIR="$HOME/flhub-runtime/vantage6/keys"
SITE="${1:?usage: make_key.sh SITE [--rotate] [--no-send|--send-only] [--collab NAME]}"; shift
ROTATE=0; SEND=1; MAKE=1; COLLAB=flhub; CONFIRM=""
while [ $# -gt 0 ]; do
  case "$1" in
    --rotate) ROTATE=1 ;; --no-send) SEND=0 ;; --send-only) MAKE=0 ;;
    --collab) COLLAB="${2:?--collab needs a value}"; shift ;;
    --confirm) CONFIRM="${2:?--confirm needs the fingerprint}"; MAKE=0; SEND=0; shift ;;
    *) echo "unknown option $1" >&2; exit 2 ;;
  esac
  shift
done
[[ "$SITE" =~ ^v6-h[0-9]+$ ]] || { echo "site name $SITE: expected v6-h<N>" >&2; exit 2; }
case "$COLLAB" in flhub|flhub-keytest) ;; *) echo "collaboration $COLLAB: only flhub or flhub-keytest" >&2; exit 2 ;; esac
log() { echo "[v6-make-key] $*"; }
mkdir -p "$KEYS_DIR"; chmod 700 "$HOME/flhub-runtime" "$HOME/flhub-runtime/vantage6" "$KEYS_DIR"
KEY="$KEYS_DIR/$SITE$([ "$COLLAB" = flhub ] || echo "@$COLLAB").key"

if [ "$MAKE" = 1 ]; then
  if [ -f "$KEY" ]; then
    [ "$ROTATE" = 1 ] || { echo "$KEY exists; use --rotate to replace it" >&2; exit 1; }
    [ ! -e "$KEY.old" ] || { echo "$KEY.old exists: a rotation is not finished (central registers the new key, then make_key.sh $SITE --confirm <fp>)" >&2; exit 1; }
    if [ -f "$KEY.registered" ]; then mv "$KEY" "$KEY.old"; rm -f "$KEY.registered"; log "old key kept as $KEY.old"
    else rm -f "$KEY"; log "replaced a key that was never confirmed as registered"; fi
  fi
  (umask 077; python3 -c 'import uuid; print(uuid.uuid4())' > "$KEY")
  chmod 600 "$KEY"
  log "new key $KEY"
fi
[ -f "$KEY" ] || { echo "no key $KEY" >&2; exit 1; }
FP="$(tr -d '[:space:]' < "$KEY" | sha256sum | cut -c1-12)"
log "key fingerprint fp=$FP"
if [ -n "$CONFIRM" ]; then
  [ "$CONFIRM" = "$FP" ] || { echo "fingerprint $CONFIRM is not this key's ($FP): not confirmed" >&2; exit 1; }
  touch "$KEY.registered"; chmod 600 "$KEY.registered"
  if [ -f "$KEY.old" ]; then shred -u "$KEY.old"; log "rotated-away key deleted"; fi
  log "$KEY marked registered; render the node config (install.sh / extra.sh install) and restart the node"
  exit 0
fi

if [ "$SEND" = 1 ]; then
  out="$(cd "$REPO" && tools/bus put "$KEY" --to central --secret --note "v6 node key $SITE fp=$FP collab=$COLLAB")"
  echo "$out" | sed 's/^/[v6-make-key] /'
  log "central registers it: tools/bus get <id> -o <file>; bash deploy/central/vantage6/register_node.sh $SITE <file>$([ "$COLLAB" = flhub ] || echo " --collab $COLLAB") --fingerprint $FP"
fi

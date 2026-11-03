#!/usr/bin/env bash
# Register a vantage6 node API key that the HOSPITAL made (deploy/vantage6-vm/make_key.sh) on the
# RUNNING server (central). Central never generates a node key. Steps:
#   1. checks: the site is allowed, the key is a UUID4, the fingerprint matches (if given)
#   2. REST API (root): organisation, collaboration (only a test collaboration is created here),
#      membership
#   3. backup of the server DB: ~/flhub-runtime/vantage6/db-backups/<ts>-<site>.sqlite (mode 600)
#   4. set_node_key.py inside the server container (docker exec -i; the key goes in on stdin):
#      creates the node with this key, or replaces the key of the existing node (rotation)
#   5. checks: the node is in the organisation and collaboration; a node login with the new key
#      works; a login with the site's old central-made key (if central still has it) gets 401
# The key file is deleted (shred -u) on exit, also when a step fails.
# Usage: bash deploy/central/vantage6/register_node.sh SITE KEYFILE [--collab NAME] [--fingerprint FP]
#   SITE      a vantage6 site of deploy/topology.env or an extra site (v6-h9 v6-h10)
#   KEYFILE   from `tools/bus get <id> -o KEYFILE` (the hospital sent it with --secret)
#   --collab  flhub (default) or flhub-keytest: the side-by-side test collaboration, created if
#             missing with hub-operator as member. One node per organisation and collaboration, so a
#             site has a second, separate node there; the live adapter (flhub) does not see it.
#   --fingerprint  the fp=<12 hex> from the hospital's bus message: refused if it differs
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../../.." && pwd)"
. "$REPO/deploy/topology.env"
NAME=flhub
RT="${V6_RT:-$HOME/flhub-runtime/vantage6}"
VENV="${V6_VENV:-$HOME/venvs/v6}"
EXTRA_SITES="v6-h9 v6-h10"
SITE="${1:?usage: register_node.sh SITE KEYFILE [--collab NAME] [--fingerprint FP]}"
KEYFILE="${2:?usage: register_node.sh SITE KEYFILE [--collab NAME] [--fingerprint FP]}"
shift 2
COLLAB=flhub; FP_EXPECTED=""
while [ $# -gt 0 ]; do
  case "$1" in
    --collab) COLLAB="$2"; shift ;;
    --fingerprint) FP_EXPECTED="$2"; shift ;;
    *) echo "unknown option $1" >&2; exit 2 ;;
  esac
  shift
done
trap 'shred -u "$KEYFILE" 2>/dev/null || rm -f "$KEYFILE"' EXIT
log() { echo "[v6-register] $*"; }
die() { echo "[v6-register] REFUSED: $*" >&2; exit 1; }

# --- 1. checks ---
[ -f "$KEYFILE" ] || die "no key file $KEYFILE"
allowed="$(for s in $SITES; do case "$s" in *:vantage6:*) echo "${s%%:*}" ;; esac; done) $EXTRA_SITES"
case " $(echo $allowed) " in *" $SITE "*) ;; *) die "site $SITE is not allowed (allowed: $(echo $allowed))" ;; esac
case "$COLLAB" in flhub|flhub-keytest) ;; *) die "collaboration $COLLAB: only flhub or flhub-keytest" ;; esac
KEY="$(tr -d '[:space:]' < "$KEYFILE")"
[[ "$KEY" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$ ]] || die "the key is not a lowercase UUID4"
FP="$(printf '%s' "$KEY" | sha256sum | cut -c1-12)"
log "site $SITE, collaboration $COLLAB, key fingerprint fp=$FP"
[ -z "$FP_EXPECTED" ] || [ "$FP_EXPECTED" = "$FP" ] || die "fingerprint $FP differs from the expected $FP_EXPECTED"
SERVER_URL="$(grep '^V6_SERVER_URL=' "$RT/adapter.env" | cut -d= -f2-)"
API="$SERVER_URL:$V6_SERVER_PORT/api"
CONTAINER="$(docker ps -q --filter "label=vantage6-type=server" --filter "label=name=$NAME")"
[ -n "$CONTAINER" ] || die "the vantage6 server container is not running"
# the site's old central-made key, for the 401 check (install.sh before this change, add_sites.sh);
# it belongs to the flhub node only
var="V6_API_KEY_$(echo "$SITE" | tr 'a-z-' 'A-Z_')"
OLD_KEY=""
[ "$COLLAB" != flhub ] || OLD_KEY="$(grep -h "^$var=" "$RT/secrets.env" "$RT/v6-nodes-extra.env" 2>/dev/null | head -1 | cut -d= -f2- || true)"

# --- 2. organisation, collaboration, membership (REST, as root) ---
pw="$(grep '^V6_ROOT_PASSWORD=' "$RT/secrets.env" | cut -d= -f2-)"
V6_ROOT_PASSWORD="$pw" "$VENV/bin/python" - "$SERVER_URL" "$V6_SERVER_PORT" "$SITE" "$COLLAB" <<'PY'
import logging, os, sys
from vantage6.client import UserClient

url, port, site, collab_name = sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4]
root = logging.getLogger(); handlers = list(root.handlers)
client = UserClient(url, port, "/api", log_level="warn")
root.handlers[:] = handlers
client.authenticate("root", os.environ["V6_ROOT_PASSWORD"])
client.setup_encryption(None)
orgs = {o["name"]: o for o in client.organization.list(per_page=100)["data"]}
org = orgs.get(site) or client.organization.create(
    name=site, address1="vantage6 VM", address2="", zipcode="0000", country="NL", domain=f"{site}.flhub.local")
collab = next((c for c in client.collaboration.list(per_page=100)["data"] if c["name"] == collab_name), None)
if collab is None:
    if collab_name == "flhub":
        sys.exit("collaboration flhub does not exist (run install.sh)")
    collab = client.collaboration.create(name=collab_name, organizations=[orgs["hub-operator"]["id"], org["id"]],
                                         encrypted=False)
    print(f"[v6-register] created test collaboration {collab_name} (id {collab['id']})")
members = {o["id"] for o in client.organization.list(collaboration=collab["id"], per_page=100)["data"]}
if org["id"] not in members:
    client.collaboration.add_organization(org["id"], collab["id"])
print(f"[v6-register] organisation {site} (id {org['id']}) is a member of {collab_name} (id {collab['id']})")
PY

# --- 3. DB backup (sqlite online backup inside the container, then copied out) ---
mkdir -p "$RT/db-backups"; chmod 700 "$RT/db-backups"
BACKUP="$RT/db-backups/$(date -u +%Y%m%dT%H%M%SZ)-$SITE.sqlite"
docker exec "$CONTAINER" python -c '
import os, sqlite3
src = sqlite3.connect(os.environ["VANTAGE6_DB_URI"].replace("sqlite:///", "", 1))
dst = sqlite3.connect("/tmp/flhub-backup.sqlite"); src.backup(dst); dst.close(); src.close()'
(umask 077; docker cp -q "$CONTAINER:/tmp/flhub-backup.sqlite" "$BACKUP")
docker exec "$CONTAINER" rm -f /tmp/flhub-backup.sqlite
chmod 600 "$BACKUP"
log "DB backup: $BACKUP"

# --- 4. the write: the key goes in on stdin ---
printf '%s\n' "$KEY" | docker exec -i "$CONTAINER" python -c "$(cat "$REPO/deploy/central/vantage6/set_node_key.py")" \
  "$SITE" "$COLLAB" 2>&1 | grep -v -i 'warning' | sed 's/^/[v6-register] /'
[ "${PIPESTATUS[1]}" = 0 ] || die "set_node_key.py failed (DB backup: $BACKUP)"

# --- 5. checks ---
node_login() {  # HTTP status of a node login with the key on stdin (never in argv)
  python3 -c 'import json,sys; print(json.dumps({"api_key": sys.stdin.read().strip()}))' \
    | curl -s -o /dev/null -w '%{http_code}' -H 'Content-Type: application/json' -d @- "$API/token/node"
}
code="$(printf '%s' "$KEY" | node_login)"
[ "$code" = 200 ] || die "node login with the new key: HTTP $code (DB backup: $BACKUP)"
log "node login with the new key: HTTP 200"
if [ -n "$OLD_KEY" ] && [ "$OLD_KEY" != "$KEY" ]; then
  code="$(printf '%s' "$OLD_KEY" | node_login)"
  [ "$code" = 401 ] || die "node login with the old central-made key: HTTP $code, expected 401"
  log "node login with the old central-made key: HTTP 401 (delete it: switch-over step 5)"
fi
V6_ROOT_PASSWORD="$pw" "$VENV/bin/python" - "$SERVER_URL" "$V6_SERVER_PORT" "$SITE" "$COLLAB" <<'PY'
import logging, os, sys
from vantage6.client import UserClient

url, port, site, collab_name = sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4]
root = logging.getLogger(); handlers = list(root.handlers)
client = UserClient(url, port, "/api", log_level="warn")
root.handlers[:] = handlers
client.authenticate("root", os.environ["V6_ROOT_PASSWORD"])
client.setup_encryption(None)
collab = next(c for c in client.collaboration.list(per_page=100)["data"] if c["name"] == collab_name)
nodes = client.node.list(collaboration=collab["id"], per_page=100)["data"]
orgs = {o["id"]: o["name"] for o in client.organization.list(per_page=100)["data"]}
mine = [n for n in nodes if orgs.get(n["organization"]["id"]) == site]
if len(mine) != 1:
    sys.exit(f"[v6-register] expected 1 node of {site} in {collab_name}, found {len(mine)}")
print(f"[v6-register] node {mine[0]['id']} '{mine[0]['name']}' of {site} in {collab_name}: status {mine[0]['status']}")
PY
log "done: $SITE registered with the hospital's key fp=$FP; key file deleted"

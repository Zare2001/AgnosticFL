#!/usr/bin/env bash
# Register extra vantage6 hospitals (E5: v6-h9 v6-h10) on the RUNNING server through the API:
# organisation, membership of collaboration flhub, node. Idempotent; the server DB is not reset
# (install.sh imports entities only once). A node is (re)created only if its API key is not in
# the keys file yet. New keys: ~/flhub-runtime/vantage6/v6-nodes-extra.env (mode 600), for the
# vantage6 VM: tools/bus put <file> --to vantage6 --secret.
# Usage: bash deploy/central/vantage6/add_sites.sh [site ...]   (default: v6-h9 v6-h10)
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../../.." && pwd)"
. "$REPO/deploy/topology.env"
RT="${V6_RT:-$HOME/flhub-runtime/vantage6}"
VENV="${V6_VENV:-$HOME/venvs/v6}"
SERVER_URL="$(grep '^V6_SERVER_URL=' "$RT/adapter.env" | cut -d= -f2-)"
KEYS="$RT/v6-nodes-extra.env"
[ $# -gt 0 ] || set -- v6-h9 v6-h10
pw="$(grep '^V6_ROOT_PASSWORD=' "$RT/secrets.env" | cut -d= -f2-)"
touch "$KEYS"; chmod 600 "$KEYS"
V6_ROOT_PASSWORD="$pw" "$VENV/bin/python" - "$SERVER_URL" "$V6_SERVER_PORT" "$KEYS" "$@" <<'PY'
import logging, os, sys
from vantage6.client import UserClient

url, port, keys_file, sites = sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4:]
root = logging.getLogger(); handlers = list(root.handlers)
client = UserClient(url, port, "/api", log_level="warn")
root.handlers[:] = handlers
client.authenticate("root", os.environ["V6_ROOT_PASSWORD"])
client.setup_encryption(None)
collab = next(c for c in client.collaboration.list(per_page=100)["data"] if c["name"] == "flhub")
keys = dict(line.strip().split("=", 1) for line in open(keys_file) if "=" in line)
for site in sites:
    orgs = {o["name"]: o for o in client.organization.list(per_page=100)["data"]}
    org = orgs.get(site) or client.organization.create(
        name=site, address1="vantage6 VM", address2="", zipcode="0000", country="NL", domain=f"{site}.flhub.local")
    members = {o["id"] for o in client.organization.list(collaboration=collab["id"], per_page=100)["data"]}
    if org["id"] not in members:
        client.collaboration.add_organization(org["id"], collab["id"])
    var = "V6_API_KEY_" + site.upper().replace("-", "_")
    nodes = [n for n in client.node.list(collaboration=collab["id"], per_page=100)["data"]
             if n["organization"]["id"] == org["id"]]
    if nodes and var in keys:
        print(f"[v6-add-sites] {site}: organisation {org['id']}, node {nodes[0]['id']} exist (key kept)")
        continue
    for n in nodes:  # its API key is lost: replace the node
        client.node.delete(n["id"])
    node = client.node.create(collaboration=collab["id"], organization=org["id"], name=site)
    keys[var] = node["api_key"]
    print(f"[v6-add-sites] {site}: organisation {org['id']}, new node {node['id']} (key in {keys_file})")
with open(keys_file, "w") as f:
    f.writelines(f"{k}={v}\n" for k, v in sorted(keys.items()))
PY

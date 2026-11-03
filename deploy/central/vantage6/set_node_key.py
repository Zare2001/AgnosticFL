# Runs INSIDE the running vantage6 4.15.2 server container (register_node.sh: docker exec -i).
# Registers a node API key that the hospital made: creates the node of organisation ORG in
# collaboration COLLAB with that key, or replaces the key of the existing node (rotation).
# The REST API cannot do this (POST /node and the key reset always generate the key), so this uses
# the server's own model, the same call as the entities import (controller/fixture.py): db.Node(...,
# api_key=key); the Node model stores only the bcrypt hash (model/node.py, _validate_api_key).
# The key is read from stdin (never argv/env). Organisation and collaboration membership must exist
# (register_node.sh makes them through the REST API first).
# Usage: python -c "$(cat set_node_key.py)" ORG COLLAB < keyfile
# Output: one line "node <id> <created|rotated> name=<name>"; exit 1 on any refusal.
import os
import re
import sys

import vantage6.server.model as db
from vantage6.common.globals import AuthStatus
from vantage6.server.model.base import Database, DatabaseSessionManager

UUID4 = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")


def fail(msg):
    print(f"set_node_key: {msg}", file=sys.stderr)
    sys.exit(1)


org_name, collab_name = sys.argv[1], sys.argv[2]
key = sys.stdin.read().strip()
if not UUID4.match(key):
    fail("the key is not a lowercase UUID4")

Database().connect(uri=os.environ["VANTAGE6_DB_URI"])
DatabaseSessionManager.new_session()
session = DatabaseSessionManager.get_session()

org = db.Organization.get_by_name(org_name)
if org is None:
    fail(f"no organisation {org_name}")
collab = session.query(db.Collaboration).filter_by(name=collab_name).one_or_none()
if collab is None:
    fail(f"no collaboration {collab_name}")
if org not in collab.organizations:
    fail(f"{org_name} is not a member of {collab_name}")

nodes = session.query(db.Node).all()
mine = [n for n in nodes if n.organization_id == org.id and n.collaboration_id == collab.id]
# node login (get_by_api_key) takes the first node whose hash matches: keys must be unique
for n in nodes:
    if n not in mine and n.check_key(key):
        fail(f"the key is already the key of node {n.id} ({n.name}); make a new one")

if mine:
    node = mine[0]
    if node.check_key(key):
        print(f"node {node.id} unchanged name={node.name} (this key is already registered)")
        sys.exit(0)
    node.api_key = key          # hashed by the model's validator
    node.save()
    action = "rotated"
else:
    node = db.Node(name=f"{org_name} - {collab_name} Node", organization=org, collaboration=collab,
                   api_key=key, status=AuthStatus.OFFLINE.value)
    node.save()
    action = "created"
session.refresh(node)
if not node.check_key(key):
    fail(f"node {node.id}: the stored hash does not match the key after the write")
print(f"node {node.id} {action} name={node.name}")

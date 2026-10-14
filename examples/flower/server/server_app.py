"""The hub's relay ServerApp for Flower, annotated (example).

[Flower] A ServerApp runs inside a Flower run on the SuperLink and reaches the SuperNodes only
through its Grid. The hub cannot send Flower messages from outside, so the Flower adapter
(adapter.py) keeps one long-lived run alive whose ServerApp is this relay. The relay loop itself
is the library's (class Relay in clients/flower/flhub_flower/server_app.py); this file shows how
a ServerApp hands its Grid to it.

What one pass of Relay.serve() does:
  1. GET /relay/next on the adapter (long poll): a round with job id, sites, global model
     reference, train config and deadline, or nothing.
  2. Load the global weights from the hub core (blob store) by their reference.
  3. Map node ids to hub sites: Grid.get_node_ids() lists the online SuperNodes; each new node
     gets one site-info QUERY, answered by the ClientApp's @app.query() handler.
  4. Grid.push_messages(): one TRAIN Message per site, content {"arrays", "config"}, ttl = the
     time left until the deadline, group_id = the hub round.
  5. Grid.pull_messages() every second: each reply's weights go to the hub core, then the site's
     result to the adapter (POST /relay/site_result; a failure: /relay/site_error).
  6. Every 5 s: GET /relay/job to drop sites the hub no longer waits for, and a fresh list of
     online nodes, so that a lost SuperNode fails its site at once instead of at the deadline.
A thread posts /relay/beat every 5 s, so the adapter notices a relay that ended with its
SuperLink and starts a new run.

The FAB's pyproject.toml names this module as serverapp (examples/flower/run_local.py builds
such a FAB). The relay imports the hub package (adapters.common), which the FAB does not carry:
install it in the environment from which the SuperLink starts its ServerApps.
"""

from __future__ import annotations

from flwr.app import Context
from flwr.serverapp import Grid, ServerApp

from adapters.common import RelayClient  # [flhub] the relay side of the adapter's HTTP routes
from flhub_flower.server_app import Relay  # [flhub] the library's relay loop (steps 1-6 above)

app = ServerApp()  # [Flower] the object the FAB's pyproject.toml names as serverapp


@app.main()
def main(grid: Grid, context: Context) -> None:
    """[Flower] Entry point of the run; it returns when the adapter asks the relay to stop."""
    # [Flower] The run config: [tool.flwr.app.config] of the FAB's pyproject.toml, overridden by
    # `flwr run --run-config`. The adapter passes its own URL, the hub core's URL and the FAB id.
    cfg = context.run_config
    client = RelayClient(str(cfg["adapter-url"]), hub_url=str(cfg["hub-url"]))
    relay = Relay(
        grid,  # [Flower] the only way to the SuperNodes
        client,
        poll_timeout_s=float(cfg["poll-timeout-s"]),  # long poll of GET /relay/next
        query_timeout_s=float(cfg["query-timeout-s"]),  # time a node gets to answer the QUERY
        fab_id=str(cfg.get("fab-id", "")),  # a relay from an older FAB is told to leave
        run_id=context.run_id,  # reported in the beat, shown by the adapter's /relay/status
    )
    relay.serve()

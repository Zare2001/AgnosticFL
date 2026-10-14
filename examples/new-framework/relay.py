"""Skeleton adapter, relay pattern: the round runs inside the framework's server process.

    python examples/new-framework/relay.py adapter --port 8821 --hub http://127.0.0.1:8800 \
        --sites nvf-h1 nvf-h2 --offline toy-h7
    python examples/new-framework/relay.py relay --adapter http://127.0.0.1:8821 --hub http://127.0.0.1:8800 \
        --sites nvf-h1 nvf-h2 --offline toy-h7

Use this pattern when only code inside the framework's server can talk to the sites: NVFlare
runs its relay as a job (adapters/nvflare/), Flower as a long-running ServerApp (adapters/flower/).
The adapter service (first command) is a RelayAdapter without any framework code. The relay
(second command) is the part you write: a loop inside the framework server that takes jobs from
the adapter, sends them to the sites with the framework's own messages and posts each site's
reply back. Weights travel as hub-core blobs, so the adapter never holds them. Lines marked
REPLACE are the framework calls; the toy framework (toy_framework.py) stands in for them.
"""

from __future__ import annotations

import argparse
import logging
import time

from adapters.common.hubclient import HubClient, HubError
from adapters.common.relay import RelayAdapter, RelayClient

from toy_framework import ToyFramework  # REPLACE: inside your framework's server process

log = logging.getLogger("toy.relay")


# ---- adapter side: the service the orchestrator talks to --------------------------------------
class ToyRelayAdapter(RelayAdapter):
    """No hooks to write: RelayAdapter queues every round as a job for the relay, serves the
    /relay/* routes on the adapter port and answers discover with the sites the relay reports."""

    adapter_name = "toy-relay"  # REPLACE
    framework = "toy"  # REPLACE
    framework_version = "0.1"  # REPLACE


# ---- relay side: runs inside the framework's server -----------------------------------------------
def run_relay(adapter_url: str, hub_url: str, fw: ToyFramework) -> None:
    relay = RelayClient(adapter_url, hub_url)
    while True:
        try:  # the relay knows which sites are connected; the adapter answers discover with it
            relay.report_sites({s: "online" if up else "offline" for s, up in fw.connected_sites().items()})
        except HubError:  # adapter not reachable (e.g. restarting); report again after the next poll
            pass
        job = relay.next_job(timeout=5)  # long poll: {"type": "train" | "idle" | "shutdown", ...}
        if job["type"] == "shutdown":
            return
        if job["type"] == "train":
            try:
                run_job(relay, fw, job)
            except HubError as exc:  # the adapter went away during the job; it forgets the job too
                log.warning("job %s abandoned: %s", job["job_id"], exc)


def run_job(relay: RelayClient, fw: ToyFramework, job: dict) -> None:
    job_id = job["job_id"]
    weights = relay.get_model(job)  # the global model, from the hub core
    # Send the weights and job["config"], unchanged, to every site of the job.
    jobs = {site: fw.submit(site, weights, job["config"]) for site in job["sites"]}  # REPLACE
    waiting = set(jobs)
    try:
        while waiting:
            status = relay.job_status(job_id)  # cancels and deadlines happen at the adapter
            if status is None or status["state"] in ("completed", "failed", "cancelled"):
                return
            for site in sorted(waiting):
                if status["sites"].get(site) != "running":  # cancelled at the adapter
                    waiting.discard(site)
                    fw.cancel(jobs[site])  # REPLACE
                    continue
                state, reply = fw.poll(jobs[site])  # REPLACE
                if state == "running":
                    continue
                waiting.discard(site)
                if state == "failed":  # one site failed; the others go on
                    relay.post_site_error(job_id, site, str(reply))
                else:  # post each site at once: it stores the weights in the hub core, sends the ref
                    relay.post_site_result(job_id, relay.result(site, reply["weights"], reply["num_examples"],
                                                                reply["metrics"], reply["code_version"]))
            time.sleep(0.2)
    finally:
        for site in waiting:  # stop what the sites still do (best effort)
            fw.cancel(jobs[site])  # REPLACE


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("role", choices=["adapter", "relay"])
    ap.add_argument("--host", default="127.0.0.1", help="adapter: address to listen on")
    ap.add_argument("--port", type=int, default=8821, help="adapter: port to listen on")
    ap.add_argument("--adapter", default="http://127.0.0.1:8821", help="relay: the adapter's URL")
    ap.add_argument("--hub", default="http://127.0.0.1:8800", help="hub core URL (blob store for the weights)")
    ap.add_argument("--sites", nargs="+", default=["nvf-h1", "nvf-h2"], help="connected sites")
    ap.add_argument("--offline", nargs="*", default=[], help="more registered sites that are not connected")
    ap.add_argument("--data-dir", default=None, help="relay: toy hospitals' data (default $FLHUB_DATA_DIR)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    sites = args.sites + args.offline
    if args.role == "adapter":
        ToyRelayAdapter(sites, HubClient(args.hub)).serve(args.host, args.port)
    else:
        run_relay(args.adapter, args.hub, ToyFramework(sites, args.offline, args.data_dir))


if __name__ == "__main__":
    main()

"""Builds the long-running NVFlare relay job ("flhub-relay") with the standard Job API.

Server app: HubRelayController (imported from the installed `adapters` package, not shipped).
Client app: the standard Client API script clients/nvflare/client.py via ScriptRunner
(in-process, numpy exchange, FULL weights). The script is the only custom code in the job.

    python -m adapters.nvflare.job --out ~/flhub-runtime/nvflare/server/jobs
"""

import argparse
import os

from nvflare.job_config.api import FedJob
from nvflare.job_config.script_runner import FrameworkType, ScriptRunner

from adapters.nvflare.relay_controller import HubRelayController

JOB_NAME = "flhub-relay"
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CLIENT_SCRIPT = os.path.join(REPO_ROOT, "clients", "nvflare", "client.py")


def build_job(out_root: str, adapter_url: str = "http://127.0.0.1:8811", hub_url: str = None, min_clients: int = 2) -> str:
    """Exports the relay job folder to <out_root>/flhub-relay and returns its path."""
    out_root = os.path.abspath(os.path.expanduser(out_root))
    job = FedJob(name=JOB_NAME, min_clients=min_clients)
    job.set_app_packages(["adapters"])  # the relay runs from the installed package on the server
    job.to_server(HubRelayController(adapter_url=adapter_url, hub_url=hub_url), id="relay")
    # A relative script path makes the job ship custom/client.py and run that copy at the
    # hospital; an absolute path would make clients run a local file at that path instead.
    cwd = os.getcwd()
    os.chdir(os.path.dirname(CLIENT_SCRIPT))
    try:
        job.to_clients(ScriptRunner(script=os.path.basename(CLIENT_SCRIPT), framework=FrameworkType.NUMPY))
        job.export_job(out_root)
    finally:
        os.chdir(cwd)
    return os.path.join(out_root, JOB_NAME)


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--out", required=True, help="directory to export the job folder into")
    p.add_argument("--adapter-url", default="http://127.0.0.1:8811")
    p.add_argument("--hub-url", default=None, help="hub core URL for the relay (default: adapters.common default)")
    p.add_argument("--min-clients", type=int, default=2)
    a = p.parse_args()
    print(build_job(a.out, a.adapter_url, a.hub_url, a.min_clients))


if __name__ == "__main__":
    main()

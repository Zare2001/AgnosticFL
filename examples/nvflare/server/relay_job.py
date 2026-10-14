"""Builds the hub's NVFlare relay job with NVFlare's Job API (example).

The relay job is one long-running NVFlare job; adapters/nvflare/job.py builds the production one.

  server app  HubRelayController, the library's relay workflow (an NVFlare ModelController): it
              long-polls the adapter for hub rounds, sends one NVFlare task per site and posts each
              reply back. The NVFlare server imports it from its installed hub package.
  client app  ../client/client.py, run in-process by NVFlare's ScriptRunner (numpy, FULL weights),
              plus the flhub_core package as custom code: the channel through which new training
              code reaches the hospitals.

    python examples/nvflare/server/relay_job.py export --out DIR [--adapter-url URL] [--hub-url URL]
    python examples/nvflare/server/relay_job.py simulate --workspace DIR [--clients nvf-h1 nvf-h2] [...]

`export` writes the job folder that the adapter submits to a real NVFlare server (adapter.py);
`simulate` runs the same job in NVFlare's simulator, with server and clients as local processes
(run_local.py).
"""

from __future__ import annotations

import argparse
import contextlib
import os
import tempfile
from pathlib import Path

from nvflare.job_config.api import FedJob
from nvflare.job_config.script_runner import FrameworkType, ScriptRunner

from adapters.nvflare.job import JOB_NAME, stage_flhub_core
from adapters.nvflare.relay_controller import HubRelayController

CLIENT_DIR = Path(__file__).resolve().parents[1] / "client"
CLIENT_SCRIPT = "client.py"


@contextlib.contextmanager
def in_client_dir():
    """NVFlare resolves the client script path when it writes the job. With a relative path the job
    ships custom/client.py and the hospital runs that copy; an absolute path would make the hospital
    run a local file at that path instead. So build and write the job from the script's directory."""
    cwd = os.getcwd()
    os.chdir(CLIENT_DIR)
    try:
        yield
    finally:
        os.chdir(cwd)


def build_relay_job(adapter_url: str, hub_url: str | None, staging: str) -> FedJob:
    """HubRelayController on the server; the example client and flhub_core on every client.
    Build, then export or simulate, inside in_client_dir()."""
    job = FedJob(name=JOB_NAME, min_clients=1)  # runs with whichever sites are connected
    job.set_app_packages(["adapters"])  # the relay comes from the server's installed hub package, not the job
    job.to_server(HubRelayController(adapter_url=adapter_url, hub_url=hub_url), id="relay")
    job.to_clients(ScriptRunner(script=CLIENT_SCRIPT, framework=FrameworkType.NUMPY))  # in-process, numpy
    job.to_clients(stage_flhub_core(staging))  # a directory: its flhub_core/*.py go to custom/
    return job


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    export, simulate = sub.add_parser("export"), sub.add_parser("simulate")
    for p in (export, simulate):
        p.add_argument("--adapter-url", default="http://127.0.0.1:8811", help="the adapter the relay polls")
        p.add_argument("--hub-url", default=None, help="hub core URL (default: $FLHUB_HUB_URL or 127.0.0.1:8800)")
    export.add_argument("--out", required=True, help="directory to write the job folder into")
    simulate.add_argument("--workspace", required=True, help="simulator workspace directory")
    simulate.add_argument("--clients", nargs="+", default=["nvf-h1", "nvf-h2"], help="client (site) names")
    args = ap.parse_args()

    target = os.path.abspath(args.out if args.cmd == "export" else args.workspace)  # before the chdir
    with tempfile.TemporaryDirectory(prefix="flhub-job-") as staging, in_client_dir():
        job = build_relay_job(args.adapter_url, args.hub_url, staging)
        if args.cmd == "export":
            job.export_job(target)
            print(os.path.join(target, JOB_NAME))
        else:
            job.simulator_run(target, clients=args.clients)  # returns when the relay ends the job


if __name__ == "__main__":
    main()

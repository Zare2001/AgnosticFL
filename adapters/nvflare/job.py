"""Builds the long-running NVFlare relay job ("flhub-relay") with the standard Job API.

Server app: HubRelayController (imported from the installed `adapters` package, not shipped).
Client app: the standard Client API script clients/nvflare/client.py via ScriptRunner
(in-process, numpy exchange, FULL weights), plus the training library flhub_core (.py files of
this checkout) as job custom code: NVFlare's channel for code updates (ADR-018). NVFlare appends
custom/ to sys.path, so the hospital environment must not install flhub_core itself.

    python -m adapters.nvflare.job --out ~/flhub-runtime/nvflare/server/jobs
"""

import argparse
import os
import shutil
import tempfile
from pathlib import Path

from nvflare.job_config.api import FedJob
from nvflare.job_config.script_runner import FrameworkType, ScriptRunner

from adapters.nvflare.relay_controller import HubRelayController

JOB_NAME = "flhub-relay"
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CLIENT_SCRIPT = os.path.join(REPO_ROOT, "clients", "nvflare", "client.py")
FLHUB_CORE_SRC = os.path.join(REPO_ROOT, "flhub_core", "src", "flhub_core")


def stage_flhub_core(parent: str) -> str:
    """Copies the flhub_core package (.py files only) to <parent>/flhub_core; returns parent."""
    src, dst = Path(FLHUB_CORE_SRC), Path(parent) / "flhub_core"
    for f in sorted(src.rglob("*.py")):
        if "__pycache__" not in f.parts:
            (dst / f.relative_to(src)).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dst / f.relative_to(src))
    return parent


def export_with_client_code(job: FedJob, out_root: str, targets: list[str] | None = None) -> None:
    """Adds client.py (ScriptRunner, in-process, numpy) and flhub_core as the clients' custom
    code (for all clients, or only `targets`), then exports the job to out_root."""
    # A relative script path makes the job ship custom/client.py and run that copy at the
    # hospital (an absolute path would make clients run a local file at that path instead).
    # NVFlare resolves it at export time, so the export runs in the script's directory.
    cwd = os.getcwd()
    with tempfile.TemporaryDirectory(prefix="flhub-job-") as staging:
        os.chdir(os.path.dirname(CLIENT_SCRIPT))
        try:
            stage_flhub_core(staging)
            for target in targets or [None]:
                runner = ScriptRunner(script=os.path.basename(CLIENT_SCRIPT), framework=FrameworkType.NUMPY)
                if target is None:
                    job.to_clients(runner)
                    job.to_clients(staging)  # a directory: its contents go to custom/
                else:
                    job.to(runner, target)
                    job.to(staging, target)
            job.export_job(out_root)
        finally:
            os.chdir(cwd)
    apps = [d for d in (Path(out_root) / job.name).iterdir() if d.is_dir() and (d / "config" / "config_fed_client.json").exists()]
    missing = [f"{d.name}/custom/{f}" for d in apps for f in ("client.py", "flhub_core/__init__.py")
               if not (d / "custom" / f).exists()]
    if not apps or missing:
        raise RuntimeError(f"job export lacks client apps or {missing} in {out_root}/{job.name}")


def build_job(out_root: str, adapter_url: str = "http://127.0.0.1:8811", hub_url: str = None, min_clients: int = 1) -> str:
    """Exports the relay job folder to <out_root>/flhub-relay and returns its path."""
    out_root = os.path.abspath(os.path.expanduser(out_root))
    job = FedJob(name=JOB_NAME, min_clients=min_clients)
    job.set_app_packages(["adapters"])  # the relay runs from the installed package on the server
    job.to_server(HubRelayController(adapter_url=adapter_url, hub_url=hub_url), id="relay")
    export_with_client_code(job, out_root)
    return os.path.join(out_root, JOB_NAME)


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--out", required=True, help="directory to export the job folder into")
    p.add_argument("--adapter-url", default="http://127.0.0.1:8811")
    p.add_argument("--hub-url", default=None, help="hub core URL for the relay (default: adapters.common default)")
    p.add_argument("--min-clients", type=int, default=1)
    a = p.parse_args()
    print(build_job(a.out, a.adapter_url, a.hub_url, a.min_clients))


if __name__ == "__main__":
    main()

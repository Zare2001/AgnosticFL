"""Runs the NVFlare example end to end on one machine, without any NVFlare deployment (example).

    python examples/nvflare/server/run_local.py [--rounds 3] [--workdir /tmp/flhub-nvflare-example] [--keep]

What runs, all on 127.0.0.1 and under --workdir:

  1. the benchmark data of the sites nvf-h1 and nvf-h2 (flhub_core.data, downloaded once, sha256-checked);
  2. the hub core (python -m hub.core) on a free port;
  3. the adapter: a plain RelayAdapter from the library, in this process. Against a real NVFlare
     server the NVFlareAdapter of adapter.py adds the FLARE API site probe and the job keeper; here
     NVFlare's simulator starts the relay job itself, so the generic relay part is all that is needed;
  4. the relay job of relay_job.py in NVFlare's simulator: HubRelayController on the server side,
     ../client/client.py on the clients nvf-h1 and nvf-h2;
  5. the hub's orchestrator for --rounds rounds of FedAvg through that adapter;
  6. the framework-free simulation of the same run (python -m hub.sim --compare), which checks that
     the global weights are identical after every round (INV-1).

Everything it starts is stopped at the end; --keep keeps the work directory with logs and runs.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import nvflare
import psutil  # an NVFlare dependency

from adapters.common.hubclient import HubClient
from adapters.common.relay import RelayAdapter
from flhub_core.data import materialize

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
SITES = ["nvf-h1", "nvf-h2"]


class SimulatorRelayAdapter(RelayAdapter):
    """The generic relay adapter under the NVFlare adapter's name; sites are online once the relay reports them."""

    adapter_name = "nvflare"
    framework = "nvflare"
    framework_version = nvflare.__version__


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_healthy(url: str, timeout_s: float = 60.0) -> None:
    end = time.monotonic() + timeout_s
    while time.monotonic() < end:
        try:
            with urllib.request.urlopen(f"{url}/health", timeout=2) as r:
                if json.load(r).get("ok"):
                    return
        except OSError:
            time.sleep(0.5)
    raise RuntimeError(f"{url} did not become healthy within {timeout_s:.0f} s")


def stop_tree(proc: subprocess.Popen) -> None:
    """Stops a process and everything it started. NVFlare's simulator runs its server and one worker
    per client as separate processes in new sessions, so they are found by their parent links."""
    try:
        tree = psutil.Process(proc.pid).children(recursive=True) + [psutil.Process(proc.pid)]
    except psutil.NoSuchProcess:
        return
    for p in tree:
        with contextlib.suppress(psutil.NoSuchProcess):
            p.terminate()
    _, alive = psutil.wait_procs(tree, timeout=30)
    for p in alive:
        with contextlib.suppress(psutil.NoSuchProcess):
            p.kill()
    psutil.wait_procs(alive, timeout=10)


def write_config(path: Path, rounds: int, hub_url: str, adapter_url: str) -> None:
    """A run configuration in the format of configs/mock-3r.yaml, with this run's ports."""
    path.write_text(
        "name: nvflare-example\n"
        f"rounds: {rounds}\n"
        "seed: 2026\n"
        "train_config: {local_epochs: 1, batch_size: 64, lr: 0.05, momentum: 0.0, seed: 2026}\n"
        f"hub_url: {hub_url}\n"
        "adapters:\n"
        f"  - {{name: nvflare, url: \"{adapter_url}\", sites: [{', '.join(SITES)}]}}\n"
        "deadline_s: 600\n"
        "online_wait_s: 300\n"
        "tags: [example]\n")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--workdir", default="/tmp/flhub-nvflare-example")
    ap.add_argument("--keep", action="store_true", help="keep the work directory (logs, runs) afterwards")
    args = ap.parse_args()

    work = Path(args.workdir).resolve()
    shutil.rmtree(work, ignore_errors=True)
    (work / "runs").mkdir(parents=True)
    os.environ["FLHUB_DATA_DIR"] = str(work / "data")  # read by the simulated clients and the orchestrator
    for site in SITES:
        print(f"data: {materialize(site)}", flush=True)

    hub_url, adapter_url = f"http://127.0.0.1:{free_port()}", f"http://127.0.0.1:{free_port()}"
    py, procs, adapter, adapter_server, sim = sys.executable, [], None, None, None
    try:
        procs.append(subprocess.Popen([py, "-m", "hub.core", "--port", hub_url.rsplit(":", 1)[1],
                                       "--root", str(work / "hubcore")], cwd=REPO, start_new_session=True,
                                      stdout=open(work / "hubcore.log", "w"), stderr=subprocess.STDOUT))
        wait_healthy(hub_url)
        adapter = SimulatorRelayAdapter(SITES, HubClient(hub_url))
        adapter_server = adapter.build_server("127.0.0.1", int(adapter_url.rsplit(":", 1)[1])).start()
        wait_healthy(adapter_url)
        print(f"hub core {hub_url}, adapter {adapter_url}", flush=True)

        sim = subprocess.Popen([py, str(HERE / "relay_job.py"), "simulate", "--workspace", str(work / "simulator"),
                                "--adapter-url", adapter_url, "--hub-url", hub_url, "--clients", *SITES],
                               cwd=REPO, start_new_session=True,  # own session: Ctrl-C reaches only this script,
                               stdout=open(work / "simulator.log", "w"), stderr=subprocess.STDOUT)  # whose finally stops all
        procs.append(sim)
        print("NVFlare simulator started (log: simulator.log)", flush=True)

        config = work / "config.yaml"
        write_config(config, args.rounds, hub_url, adapter_url)
        run = subprocess.run([py, "-m", "hub.orchestrator", "run", "--config", str(config),
                              "--runs-dir", str(work / "runs")], cwd=REPO, capture_output=True, text=True)
        print(run.stdout[-1500:], flush=True)
        if run.returncode != 0:
            print(run.stderr[-3000:], file=sys.stderr)
            return 1

        adapter.request_relay_shutdown()  # the relay ends the job at its next poll; the simulator exits
        sim.wait(timeout=120)
        print(f"NVFlare simulator exited with {sim.returncode}", flush=True)

        check = subprocess.run([py, "-m", "hub.sim", "--config", str(config), "--runs-dir", str(work / "runs"),
                                "--compare", str(work / "runs" / "nvflare-example-latest")],
                               cwd=REPO, capture_output=True, text=True)
        print("\n".join(line for line in check.stdout.splitlines() + check.stderr.splitlines() if "INV-1" in line))
        return check.returncode
    finally:
        if sim is not None and sim.poll() is None and adapter is not None:  # e.g. after a failure or Ctrl-C:
            adapter.request_relay_shutdown()  # the normal way out, as on success: the relay ends the job
            with contextlib.suppress(subprocess.TimeoutExpired):
                sim.wait(timeout=60)
        for p in reversed(procs):  # whatever still runs, with all its children
            stop_tree(p)
        if adapter_server is not None:
            adapter_server.stop()
        if not args.keep:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())

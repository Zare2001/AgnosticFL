"""Run the Flower examples end to end on one machine, without the testbed.

    python examples/flower/run_local.py [--rounds 3] [--keep]

Everything runs on 127.0.0.1 on free ports, in one new directory under the system's temporary
directory, and is stopped and removed at the end (--keep leaves the directory with all logs):
  - the hub core (python -m hub.core);
  - a Flower SuperLink (flower-superlink, without TLS, with its own database);
  - the example adapter (server/adapter.py). It stages a FAB from the library's Flower app
    (clients/flower) in which the example ServerApp (server/server_app.py) and the example
    ClientApp (client/client_app.py) replace the library's, and starts the relay run;
  - two SuperNodes (flower-supernode) for the benchmark sites flw-h5 and flw-h6, started as a
    hospital starts them: SuperLink address and node config site-id and data-path.
The orchestrator then trains --rounds rounds through Flower, hub.sim runs the same configuration
without any framework, and the two are compared round by round (the equivalence check INV-1).
Exit 0 only if the run finished and every round's global weights equal the simulation's.

Needs the environment of docs/getting-started.md plus flwr==1.39.0 (deploy/versions.env), and
internet access once for the 4 MB PneumoniaMNIST file. Flower's simulation engine is not used:
its simulated nodes get only partition-id and num-partitions as node config, while the hub's
relay identifies hospitals by the site-id of a real SuperNode.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
BIN = Path(sys.executable).parent  # flower-superlink, flower-supernode and flwr live next to python
SITES = ["flw-h5", "flw-h6"]  # benchmark sites: flhub_core.data writes their PneumoniaMNIST partitions


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def port_open(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def get_json(url: str, payload: dict | None = None) -> dict:
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(url, data, {"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=10))


def wait_until(what: str, ready, timeout: float) -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            if ready():
                return
        except OSError:
            pass
        time.sleep(1)
    raise TimeoutError(f"no {what} after {timeout:.0f} s")


class Processes:
    """Background processes, each in its own session, so that it stops together with its children."""

    def __init__(self, logs: Path, env: dict) -> None:
        self.logs, self.env, self.procs = logs, env, {}

    def start(self, name: str, cmd: list, cwd: Path | None = None, **env) -> None:
        log = open(self.logs / f"{name}.log", "w")
        self.procs[name] = subprocess.Popen([str(c) for c in cmd], stdout=log, stderr=subprocess.STDOUT,
                                            stdin=subprocess.DEVNULL, cwd=cwd, env={**self.env, **env},
                                            start_new_session=True)

    def stop(self, name: str) -> None:
        proc = self.procs.pop(name, None)
        if proc is None or proc.poll() is not None:
            return
        os.killpg(proc.pid, signal.SIGTERM)
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()

    def tails(self) -> None:
        for log in sorted(self.logs.glob("*.log")):
            lines = log.read_text(errors="replace").splitlines()[-15:]
            print(f"--- {log.name}\n" + "\n".join(lines))


def run(rounds: int, work: Path) -> bool:
    data, app, home, runs, logs = (work / d for d in ("data", "app", "flwr-home", "runs", "logs"))
    for d in (data, home, logs):
        d.mkdir(parents=True)
    env = {**os.environ, "PATH": f"{BIN}{os.pathsep}{os.environ.get('PATH', '')}", "FLHUB_DATA_DIR": str(data)}
    py = sys.executable

    # Site data: the stage-1 partitions of flw-h5 and flw-h6, written and sha256-checked by flhub_core.
    subprocess.run([py, "-m", "flhub_core.data", "materialize", "--sites", *SITES, "--out", str(data)],
                   env=env, check=True, stdout=subprocess.DEVNULL)

    # The Flower app for the FAB: the library's app, with the example ServerApp and ClientApp.
    shutil.copytree(REPO / "clients" / "flower", app, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy(HERE / "server" / "server_app.py", app / "server_app.py")
    shutil.copy(HERE / "client" / "client_app.py", app / "client_app.py")
    toml = (app / "pyproject.toml").read_text()
    for old, new in (('serverapp = "flhub_flower.server_app:app"', 'serverapp = "server_app:app"'),
                     ('clientapp = "flhub_flower.client_app:app"', 'clientapp = "client_app:app"')):
        assert toml.count(old) == 1, f"not found in clients/flower/pyproject.toml: {old}"
        toml = toml.replace(old, new)
    (app / "pyproject.toml").write_text(toml)

    hub_port, fleet, http, adapter_port = free_port(), free_port(), free_port(), free_port()
    hub_url, adapter_url = f"http://127.0.0.1:{hub_port}", f"http://127.0.0.1:{adapter_port}"
    # The adapter's flwr connection: the example config.toml with this SuperLink's HTTP API port.
    conf = (HERE / "server" / "config.toml").read_text()
    assert conf.count('address = "127.0.0.1:9093"') == 1
    (home / "config.toml").write_text(conf.replace("127.0.0.1:9093", f"127.0.0.1:{http}"))

    procs = Processes(logs, env)
    try:
        procs.start("hubcore", [py, "-m", "hub.core", "--port", hub_port, "--root", work / "hubcore"])
        wait_until("hub core", lambda: get_json(hub_url + "/health")["ok"], 60)
        procs.start("superlink", [BIN / "flower-superlink", "--insecure", "--fleet-api-address",
                                  f"127.0.0.1:{fleet}", "--host", "127.0.0.1", "--port", http,
                                  "--database", work / "superlink.db", "--disable-runtime-dependency-installation"],
                    FLWR_HOME=str(home))
        wait_until("SuperLink", lambda: port_open(http) and port_open(fleet), 60)
        procs.start("adapter", [py, HERE / "server" / "adapter.py", "--port", adapter_port, "--sites", *SITES,
                                "--hub", hub_url, "--app-dir", app, "--flwr-home", home, "--connection", "flhub"])
        wait_until("adapter", lambda: get_json(adapter_url + "/health")["ok"], 60)
        for site in SITES:  # as a hospital starts its SuperNode, each with its own FLWR_HOME
            node_dir = work / site
            node_dir.mkdir()
            procs.start(site, [BIN / "flower-supernode", "--insecure", "--superlink", f"127.0.0.1:{fleet}",
                               "--port", free_port(),
                               "--node-config", f"site-id='{site}' data-path='{data / site}.csv'"],
                        cwd=node_dir, FLWR_HOME=str(node_dir))

        def online() -> bool:
            disc = get_json(adapter_url + "/rpc", {"jsonrpc": "2.0", "id": 1, "method": "discover",
                                                   "params": {"protocol": "flhub/0.2"}})["result"]
            return all(s["status"] == "online" for s in disc["sites"])

        t0 = time.monotonic()
        wait_until("online sites (relay run and site-info queries)", online, 240)
        print(f"relay run started, {' and '.join(SITES)} online after {time.monotonic() - t0:.0f} s", flush=True)

        config = work / "flower-local.yaml"
        config.write_text(
            "name: flower-local\n"
            f"rounds: {rounds}\nseed: 2026\n"
            "train_config: {local_epochs: 1, batch_size: 64, lr: 0.05, momentum: 0.0, seed: 2026}\n"
            f"hub_url: {hub_url}\n"
            f"adapters:\n  - {{name: flower, url: \"{adapter_url}\", sites: [{', '.join(SITES)}]}}\n"
            "deadline_s: 300\nonline_wait_s: 120\ntags: [dev]\n")
        orch = subprocess.run([py, "-m", "hub.orchestrator", "run", "--config", config, "--runs-dir", runs],
                              env=env, timeout=1800)
        sim = subprocess.run([py, "-m", "hub.sim", "--config", config, "--runs-dir", runs,
                              "--compare", runs / "flower-local-latest"],
                             env=env, timeout=1800, capture_output=True, text=True)
        print(sim.stderr + sim.stdout, end="")
        ok = orch.returncode == 0 and sim.returncode == 0 and "-> PASS" in sim.stdout
        if not ok:
            procs.tails()
        return ok
    except Exception:
        procs.tails()
        raise
    finally:
        for name in ["adapter", *SITES, "superlink", "hubcore"]:  # the adapter first: its relay ends cleanly
            procs.stop(name)


def main() -> int:
    ap = argparse.ArgumentParser(prog="examples/flower/run_local.py")
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--keep", action="store_true", help="keep the working directory (logs, run directories)")
    args = ap.parse_args()
    work = Path(tempfile.mkdtemp(prefix="flhub-flower-example-"))
    print(f"working directory {work}", flush=True)
    try:
        ok = run(args.rounds, work)
    finally:
        if not args.keep:
            shutil.rmtree(work, ignore_errors=True)
    print("flower example: PASS" if ok else "flower example: FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

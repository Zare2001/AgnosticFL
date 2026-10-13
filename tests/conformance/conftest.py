"""Options and fixtures for the flhub/0.1 adapter conformance suite.

    pytest tests/conformance --adapter http://127.0.0.1:8811 [--hub http://127.0.0.1:8800]
           [--sites nvf-h1 nvf-h2] [--data-dir ~/flhub-data] [--timeout 900] [--report out.json] -v -s
    pytest tests/conformance --adapter mock      # self-test: in-process hub core + mock adapter

Without --adapter the suite is skipped.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from flhub_core.data import SITES, load_site, materialize, site_path
from flhub_core.model import init_weights
from flhub_core.weights import spec

from adapters.common import AdapterClient, HubClient

REPORT: dict = {"tests": {}}


def pytest_addoption(parser):
    g = parser.getgroup("flhub conformance")
    g.addoption("--adapter", default=None, help="adapter base URL, or 'mock' for an in-process self-test")
    g.addoption("--hub", default="http://127.0.0.1:8800", help="hub core URL the adapter uses")
    g.addoption("--sites", nargs="+", default=None, help="sites to test (default: all from discover)")
    g.addoption("--data-dir", default=None, help="where site CSVs are (materialized if missing)")
    g.addoption("--timeout", type=float, default=900.0, help="seconds to wait for a round")
    g.addoption("--online-wait", type=float, default=120.0, help="seconds to wait for all sites online")
    g.addoption("--report", default=None, help="write a JSON summary here")


def pytest_collection_modifyitems(config, items):
    if config.getoption("--adapter"):
        return
    skip = pytest.mark.skip(reason="conformance suite needs --adapter URL (or --adapter mock)")
    for item in items:
        if "conformance" in str(item.fspath):
            item.add_marker(skip)


def pytest_sessionfinish(session, exitstatus):
    path = session.config.getoption("--report", default=None)
    if path and REPORT["tests"]:
        REPORT["exitstatus"] = int(exitstatus)
        Path(path).write_text(json.dumps(REPORT, indent=1))


@pytest.fixture(scope="session")
def opts(request):
    return request.config.option


@pytest.fixture(scope="session")
def endpoints(opts, tmp_path_factory):
    """(adapter_url, hub_url); starts an in-process hub core + mock adapter for --adapter mock."""
    if opts.adapter != "mock":
        yield opts.adapter, opts.hub
        return
    from adapters.mock.service import MockAdapter
    from hub.core.server import HubCore

    hub_srv = HubCore(tmp_path_factory.mktemp("hubcore")).build_server("127.0.0.1", 0).start()
    sites = opts.sites or ["nvf-h1", "nvf-h2"]
    for s in sites:
        if not site_path(s, opts.data_dir).exists():
            materialize(s, opts.data_dir)
    adapter_srv = MockAdapter(sites, opts.data_dir, HubClient(hub_srv.url)).build_server("127.0.0.1", 0).start()
    yield adapter_srv.url, hub_srv.url
    adapter_srv.stop()
    hub_srv.stop()


@pytest.fixture(scope="session")
def client(endpoints):
    return AdapterClient(endpoints[0], timeout=60)


@pytest.fixture(scope="session")
def hub(endpoints):
    h = HubClient(endpoints[1])
    h.health()
    return h


@pytest.fixture(scope="session")
def discovered(client):
    d = client.discover()
    REPORT["adapter"] = {k: d.get(k) for k in ("adapter", "framework", "framework_version", "protocol")}
    return d


@pytest.fixture(scope="session")
def sites(opts, discovered):
    s = opts.sites or [x["site_id"] for x in discovered["sites"]]
    REPORT["sites"] = s
    return s


@pytest.fixture(scope="session")
def site_data(opts, sites):
    """The same partitions the remote sites hold, for the direct-training reference."""
    out = {}
    for s in sites:
        if s not in SITES:
            pytest.fail(f"site {s} is not one of the project's sites {sorted(SITES)}")
        path = site_path(s, opts.data_dir)
        if not path.exists():
            path = materialize(s, opts.data_dir)
        out[s] = load_site(path)
    return out


@pytest.fixture(scope="session")
def global_model(hub):
    """(ref, weights) of the seeded initial model, registered at the hub core with its spec."""
    w = init_weights(2026)
    hub.put_spec(spec(w))
    return hub.put_weights(w), w

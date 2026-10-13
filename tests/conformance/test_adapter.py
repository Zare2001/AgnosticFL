"""flhub/0.1 adapter conformance suite: the admission test for every adapter (PROJECT_PLAN.md §6 P1).

    pytest tests/conformance/test_adapter.py --adapter http://127.0.0.1:<port> -v -s
    python tests/conformance/test_adapter.py --adapter http://127.0.0.1:<port>     (same thing)

The central check: one dispatched round returns one envelope per site whose weights equal
flhub_core.train run directly on the same partition with the same config (max abs diff <= 1e-6).
"""

from __future__ import annotations

import time

import pytest
from conftest import REPORT
from flhub_core.train import train
from flhub_core.weights import max_abs_diff, spec

from adapters.common import Envelope, RpcError, envelope_problems, train_config_hash, weights_problems
from adapters.common.hubclient import http_request
from adapters.common.protocol import SITE_STATES, TASK_STATES, client_config

TRAIN_CONFIG = {"local_epochs": 1, "batch_size": 64, "lr": 0.05, "momentum": 0.0, "seed": 2026}
TOLERANCE = 1e-6


def wait_terminal(client, task_id, timeout):
    """Poll tasks/get until a terminal state; checks every status object on the way."""
    end, last = time.time() + timeout, None
    while time.time() < end:
        st = client.get_task(task_id)
        assert st["task_id"] == task_id and st["state"] in TASK_STATES, st
        assert set(st["progress"]) >= {"done", "total"}, st
        last = st
        if st["state"] in ("completed", "failed", "cancelled"):
            return st
        time.sleep(0.5)
    pytest.fail(f"task {task_id} not finished after {timeout}s; last status {last}")


def run_round(client, model_ref, hub, rnd, sites, timeout):
    t0 = time.time()
    task_id = client.dispatch(rnd, {"ref": model_ref, "url": hub.blob_url(model_ref)}, TRAIN_CONFIG, sites)
    assert time.time() - t0 < 5, "dispatch must return within 5 s"
    st = wait_terminal(client, task_id, timeout)
    assert st["state"] == "completed", f"round {rnd} ended {st['state']}: {st['error']}"
    assert st["progress"]["done"] == st["progress"]["total"] == len(sites)
    return task_id, time.time() - t0


def check_envelopes(envs, hub, rnd, model_ref, model_w, sites, site_data):
    assert [e["site_id"] for e in envs] == sites, "one envelope per site, in dispatch order"
    results = {}
    for env_d in envs:
        env = Envelope.from_dict(env_d)
        assert envelope_problems(env, round=rnd, base_model=model_ref, expected_sites=sites,
                                 train_config_hash=train_config_hash(TRAIN_CONFIG)) == []
        assert {"train_loss", "train_acc"} <= set(env.metrics), env.metrics
        got = hub.get_weights(env.weights)
        assert weights_problems(got, spec(model_w)) == []
        want, n, metrics = train(model_w, site_data[env.site_id], client_config(TRAIN_CONFIG, rnd))
        diff = max_abs_diff(got, want)
        results[env.site_id] = {"max_abs_diff": diff, "num_examples": env.num_examples,
                                "train_loss": env.metrics["train_loss"],
                                "client_code_version": env.client_code_version}
        print(f"  round {rnd} {env.site_id}: max_abs_diff={diff:.3e} n={env.num_examples} "
              f"train_loss={env.metrics['train_loss']:.6f} code={env.client_code_version}")
        assert diff <= TOLERANCE, f"{env.site_id}: weights differ from direct flhub_core.train by {diff}"
        assert env.num_examples == n
        assert abs(env.metrics["train_loss"] - metrics["train_loss"]) <= 1e-6
    return results


def test_health(endpoints):
    status, _ = http_request(endpoints[0].rstrip("/") + "/health")
    assert status == 200


def test_discover_schema(discovered):
    d = discovered
    assert d["protocol"] == "flhub/0.1"
    assert isinstance(d["adapter"], str) and isinstance(d["framework"], str)
    assert isinstance(d["framework_version"], str) and d["framework_version"]
    assert isinstance(d["sites"], list) and d["sites"]
    for s in d["sites"]:
        assert isinstance(s["site_id"], str) and s["status"] in SITE_STATES, s
    caps = d["capabilities"]
    assert caps["payload"] == "full_weights" and "float32" in caps["dtypes"]
    for key in ("max_payload_mb", "evaluate", "min_round_interval_s"):
        assert key in caps


def test_all_sites_online(client, sites, opts):
    end = time.time() + opts.online_wait
    while True:
        status = {s["site_id"]: s["status"] for s in client.discover()["sites"]}
        if all(status.get(s) == "online" for s in sites):
            return
        if time.time() > end:
            pytest.fail(f"sites not online after {opts.online_wait}s: {status}")
        time.sleep(2)


def test_error_codes(client, sites, global_model):
    ref, _ = global_model
    cases = [
        (lambda: client.call("discover", protocol="flhub/9.9"), -32010),
        (lambda: client.call("no/such/method"), -32601),
        (lambda: client.dispatch(1, {"ref": ref}, TRAIN_CONFIG, sites + ["no-such-site"]), -32011),
        (lambda: client.get_task("no-such-task"), -32012),
        (lambda: client.collect("no-such-task"), -32012),
    ]
    for call, code in cases:
        with pytest.raises(RpcError) as e:
            call()
        assert e.value.code == code, f"expected {code}, got {e.value.code}: {e.value.message}"


def test_round_matches_direct_training(client, hub, sites, site_data, global_model, opts):
    ref, w0 = global_model
    task_id, seconds = run_round(client, ref, hub, 1, sites, opts.timeout)
    envs = client.collect(task_id)
    results = check_envelopes(envs, hub, 1, ref, w0, sites, site_data)
    assert client.collect(task_id) == envs, "collect must be idempotent"
    assert client.cancel(task_id)["state"] == "completed", "cancel of a finished task changes nothing"
    REPORT["tests"]["round_1"] = {"seconds": round(seconds, 1), "sites": results}


def test_busy_then_second_round(client, hub, sites, site_data, global_model, opts):
    """A second dispatch for the same sites while one runs is -32015; afterwards rounds continue."""
    ref, w0 = global_model
    first = client.dispatch(2, {"ref": ref}, TRAIN_CONFIG, sites)
    try:
        second = client.dispatch(2, {"ref": ref}, TRAIN_CONFIG, sites)
    except RpcError as e:
        assert e.code == -32015, f"expected -32015 (busy), got {e.code}: {e.message}"
        second = None
    else:  # only acceptable if the first task had already finished
        assert client.get_task(first)["state"] in ("completed", "failed", "cancelled")
    st = wait_terminal(client, first, opts.timeout)
    assert st["state"] == "completed", st
    if second:
        wait_terminal(client, second, opts.timeout)
    results = check_envelopes(client.collect(first), hub, 2, ref, w0, sites, site_data)
    REPORT["tests"]["round_2"] = {"sites": results}


def test_cancel_then_recover(client, hub, sites, site_data, global_model, opts):
    """cancel works, and the adapter still runs the next round correctly afterwards."""
    ref, w0 = global_model
    task_id = client.dispatch(3, {"ref": ref}, TRAIN_CONFIG, sites)
    assert client.cancel(task_id)["state"] in ("cancelled", "completed")
    assert client.get_task(task_id)["state"] in ("cancelled", "completed")
    end = time.time() + opts.timeout
    while True:  # the framework may still be finishing the cancelled work
        try:
            next_id = client.dispatch(4, {"ref": ref}, TRAIN_CONFIG, sites)
            break
        except RpcError as e:
            assert e.code == -32015, f"unexpected error {e.code}: {e.message}"
            assert time.time() < end, "adapter stayed busy after cancel"
            time.sleep(2)
    st = wait_terminal(client, next_id, opts.timeout)
    assert st["state"] == "completed", st
    results = check_envelopes(client.collect(next_id), hub, 4, ref, w0, sites, site_data)
    REPORT["tests"]["round_4_after_cancel"] = {"sites": results}


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-v", "-s", *sys.argv[1:]]))

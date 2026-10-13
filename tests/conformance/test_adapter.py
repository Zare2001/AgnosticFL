"""flhub/0.2 adapter conformance suite (v2): the admission test for every adapter.

    pytest tests/conformance/test_adapter.py --adapter http://127.0.0.1:<port> -v -s
    python tests/conformance/test_adapter.py --adapter http://127.0.0.1:<port>     (same thing)

The central check: one dispatched round returns one envelope per site whose weights equal
flhub_core.train run directly on the same partition with the same config (max abs diff <= 1e-6).
v2 adds (PLAN.md stage 2 §4.4): subset dispatch; partial collect + per-site cancel, then an
immediate next dispatch; a registered but offline site does not block the others; the adapter
survives its own restart between rounds (--restart-cmd); one payload-64 echo round and one
data-variant round (both need flhub_core 0.2 at the sites).
"""

from __future__ import annotations

import time

import pytest
from conftest import REPORT
from flhub_core.train import train
from flhub_core.weights import max_abs_diff, spec

from adapters.common import Envelope, RpcError, envelope_problems, train_config_hash, weights_problems
from adapters.common.hubclient import http_request
from adapters.common.protocol import SITE_STATES, SITE_TASK_STATES, TASK_STATES, client_config

TRAIN_CONFIG = {"local_epochs": 1, "batch_size": 64, "lr": 0.05, "momentum": 0.0, "seed": 2026}
TOLERANCE = 1e-6


def wait_terminal(client, task_id, timeout):
    """Poll tasks/get until a terminal state; checks every status object on the way."""
    end, last = time.time() + timeout, None
    while time.time() < end:
        st = client.get_task(task_id)
        assert st["task_id"] == task_id and st["state"] in TASK_STATES, st
        assert set(st["progress"]) >= {"done", "total"}, st
        assert isinstance(st.get("sites"), dict) and st["sites"], f"flhub/0.2 per-site progress missing: {st}"
        assert all(v in SITE_TASK_STATES for v in st["sites"].values()), st
        last = st
        if st["state"] in ("completed", "failed", "cancelled"):
            return st
        time.sleep(0.5)
    pytest.fail(f"task {task_id} not finished after {timeout}s; last status {last}")


def run_round(client, model_ref, hub, rnd, sites, timeout, train_config=TRAIN_CONFIG):
    t0 = time.time()
    task_id = client.dispatch(rnd, {"ref": model_ref, "url": hub.blob_url(model_ref)}, train_config, sites,
                              data_variant=train_config.get("data_variant"))
    assert time.time() - t0 < 5, "dispatch must return within 5 s"
    st = wait_terminal(client, task_id, timeout)
    assert st["state"] == "completed", f"round {rnd} ended {st['state']}: {st['error']}"
    assert st["progress"]["done"] == st["progress"]["total"] == len(sites)
    return task_id, time.time() - t0


def check_envelopes(envs, hub, rnd, model_ref, model_w, sites, site_data, train_config=TRAIN_CONFIG):
    assert [e["site_id"] for e in envs] == sites, "one envelope per site, in dispatch order"
    results = {}
    for env_d in envs:
        env = Envelope.from_dict(env_d)
        assert envelope_problems(env, round=rnd, base_model=model_ref, expected_sites=sites,
                                 train_config_hash=train_config_hash(train_config), protocol="flhub/0.2") == []
        assert {"train_loss", "train_acc"} <= set(env.metrics), env.metrics
        got = hub.get_weights(env.weights)
        assert weights_problems(got, spec(model_w)) == []
        want, n, metrics = train(model_w, site_data[env.site_id], client_config(train_config, rnd))
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


def test_discover_schema(discovered, client):
    d = discovered
    assert d["protocol"] == "flhub/0.2" and "flhub/0.1" in d["protocols"]
    assert client.call("discover", protocol="flhub/0.1")["protocol"] == "flhub/0.1", "0.1 callers still served"
    assert isinstance(d["adapter"], str) and isinstance(d["framework"], str)
    assert isinstance(d["framework_version"], str) and d["framework_version"]
    assert isinstance(d["sites"], list) and d["sites"]
    for s in d["sites"]:
        assert isinstance(s["site_id"], str) and s["status"] in SITE_STATES, s
    caps = d["capabilities"]
    assert caps["payload"] == "full_weights" and "float32" in caps["dtypes"]
    for key in ("max_payload_mb", "evaluate", "min_round_interval_s"):
        assert key in caps
    assert caps["max_payload_mb"] >= 1024, "flhub/0.2: payload cap >= 1024 MB"
    assert caps.get("site_progress") and caps.get("partial_collect") and caps.get("site_cancel"), caps


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


# ---- conformance v2 (flhub/0.2) -------------------------------------------------------------------
def test_subset_dispatch(client, hub, sites, site_data, global_model, opts):
    """A dispatch to one of the adapter's sites trains only that site."""
    ref, w0 = global_model
    one = sites[-1:]
    task_id, _ = run_round(client, ref, hub, 5, one, opts.timeout)
    results = check_envelopes(client.collect(task_id), hub, 5, ref, w0, one, site_data)
    REPORT["tests"]["subset_round_5"] = {"sites": results}


def test_partial_collect_site_cancel_then_next_dispatch(client, hub, sites, site_data, global_model, opts):
    """Cancel one running site, collect the others partially, then dispatch the next round at once."""
    if len(sites) < 2:
        pytest.skip("needs two sites")
    ref, w0 = global_model
    keep, drop = sites[:-1], sites[-1]
    task_id = client.dispatch(6, {"ref": ref}, TRAIN_CONFIG, sites)
    res = client.cancel(task_id, sites=[drop])
    assert res["sites"][drop] in ("cancelled", "done"), res
    st = wait_terminal(client, task_id, opts.timeout)
    assert all(st["sites"][s] == "done" for s in keep), st
    part = client.collect_partial(task_id)
    assert part["partial"] is True and part["state"] == st["state"]
    done = [s for s in sites if part["sites"][s] == "done"]
    results = check_envelopes(part["envelopes"], hub, 6, ref, w0, done, site_data)
    if res["sites"][drop] == "cancelled":
        assert st["state"] == "cancelled" and drop not in done, st
        with pytest.raises(RpcError) as e:
            client.collect(task_id)
        assert e.value.code == -32013
    t0 = time.time()
    next_id = client.dispatch(7, {"ref": ref}, TRAIN_CONFIG, sites)  # must not be -32015
    st = wait_terminal(client, next_id, opts.timeout)
    assert st["state"] == "completed", st
    results7 = check_envelopes(client.collect(next_id), hub, 7, ref, w0, sites, site_data)
    REPORT["tests"]["partial_round_6"] = {"cancelled": drop, "drop_state": res["sites"][drop], "sites": results}
    REPORT["tests"]["round_7_after_site_cancel"] = {"seconds": round(time.time() - t0, 1), "sites": results7}


def test_offline_site_does_not_block(client, hub, sites, site_data, global_model, opts):
    """A registered but offline site (e.g. an E5 extra site) does not block a dispatch to the others."""
    status = {s["site_id"]: s["status"] for s in client.discover()["sites"]}
    offline = [s for s, st in status.items() if s not in sites and st != "online"]
    if not offline:
        pytest.skip("the adapter lists no registered-but-offline site")
    ref, w0 = global_model
    task_id, seconds = run_round(client, ref, hub, 8, sites, opts.timeout)
    results = check_envelopes(client.collect(task_id), hub, 8, ref, w0, sites, site_data)
    REPORT["tests"]["round_8_with_offline"] = {"offline": offline, "seconds": round(seconds, 1), "sites": results}


def test_adapter_restart_between_rounds(client, hub, sites, site_data, global_model, opts, restart):
    if restart is None:
        pytest.skip("needs --restart-cmd")
    ref, w0 = global_model
    run_round(client, ref, hub, 9, sites, opts.timeout)
    t0 = time.time()
    restart()
    end = time.time() + opts.online_wait
    while True:
        try:
            status = {s["site_id"]: s["status"] for s in client.discover()["sites"]}
            if all(status.get(s) == "online" for s in sites):
                break
        except RpcError:
            pass
        if time.time() > end:
            pytest.fail(f"sites not online {opts.online_wait}s after the restart")
        time.sleep(2)
    back = time.time() - t0
    task_id, _ = run_round(client, ref, hub, 10, sites, opts.timeout)
    results = check_envelopes(client.collect(task_id), hub, 10, ref, w0, sites, site_data)
    REPORT["tests"]["round_10_after_restart"] = {"online_after_s": round(back, 1), "sites": results}


def test_payload_64_echo(client, hub, sites, site_data, opts):
    """One payload-64 round: 64 MiB in 16 tensors each way per site; the echo must be exact."""
    from flhub_core.model import init_weights

    cfg = {**TRAIN_CONFIG, "model": "payload-64"}
    w0 = init_weights(2026, "payload-64")
    ref = hub.put_weights(w0)
    task_id, seconds = run_round(client, ref, hub, 11, sites, opts.timeout, train_config=cfg)
    results = check_envelopes(client.collect(task_id), hub, 11, ref, w0, sites, site_data, train_config=cfg)
    REPORT["tests"]["payload_64_round_11"] = {"seconds": round(seconds, 1), "sites": results}


def test_data_variant_round(client, hub, sites, opts):
    """One round on a stage-2 data variant (--variant): sites resolve it to the provisioned partition."""
    if not opts.variant:
        pytest.skip("needs --variant <dataset>-<pid>")
    from flhub_core.data import dataset_info, parse_variant, variant_site_data
    from flhub_core.model import init_weights

    cfg = {**TRAIN_CONFIG, "data_variant": opts.variant}
    w0 = init_weights(2026, "cnn-small", dataset_info(parse_variant(opts.variant)[0]).num_classes)
    ref = hub.put_weights(w0)
    data = variant_site_data(opts.variant, sites)
    task_id, seconds = run_round(client, ref, hub, 12, sites, opts.timeout, train_config=cfg)
    envs = client.collect(task_id)
    results = check_envelopes(envs, hub, 12, ref, w0, sites, data, train_config=cfg)
    for env in envs:
        assert env["metrics"].get("data_fp") == int(data[env["site_id"]].fingerprint[:8], 16), env["metrics"]
    REPORT["tests"]["variant_round_12"] = {"variant": opts.variant, "seconds": round(seconds, 1), "sites": results}


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-v", "-s", *sys.argv[1:]]))

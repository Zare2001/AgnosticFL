"""Hub relay ServerApp. Runs only on the central VM, never on a hospital.

One long-lived Flower run. The loop asks the Flower adapter service for the next
dispatched round (``POST /relay/next``), sends standard TRAIN messages with the
global weights to the hospital SuperNodes through the ``Grid`` and posts every reply
back to the adapter (``POST /relay/result``). Weights move between the relay and the
hub core by blob reference (PROTOCOL.md §4); the adapter builds the envelopes.

SuperNode ids change whenever a SuperNode restarts, so the relay maps node ids to
site ids by sending a QUERY message to every new node (answered by the ClientApp's
``@app.query()`` handler with its ``site-id`` node config).
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from typing import Any

from flhub_core.weights import from_safetensors, sha256_ref, to_safetensors
from flwr.app import ConfigRecord, Context, Message, MessageType, RecordDict
from flwr.serverapp import Grid, ServerApp

from flhub_flower.records import (
    ARRAYS,
    CONFIG,
    METRICS,
    QUERY_SITE_INFO,
    SITE_INFO,
    arrayrecord_to_weights,
    weights_to_arrayrecord,
)

log = logging.getLogger("flwr.flhub.relay")

app = ServerApp()

NODE_REFRESH_S = 5.0  # how often the node -> site map is refreshed while idle
QUERY_RETRY_S = 30.0  # back-off for nodes that did not answer the site-info query
PULL_INTERVAL_S = 1.0
HEARTBEAT_S = 10.0  # progress post interval while a round is running
ADAPTER_LOST_S = 600.0  # exit if the adapter is unreachable this long


class AdapterGone(Exception):
    """The adapter has been unreachable for too long."""


class Relay:
    """Moves flhub rounds between the adapter service and the Flower Grid."""

    def __init__(
        self,
        grid: Grid,
        adapter_url: str,
        relay_id: str,
        poll_timeout_s: float = 5.0,
        query_timeout_s: float = 60.0,
    ) -> None:
        self.grid = grid
        self.adapter_url = adapter_url.rstrip("/")
        self.relay_id = relay_id
        self.poll_timeout_s = poll_timeout_s
        self.query_timeout_s = query_timeout_s
        self.sites: dict[int, dict[str, Any]] = {}  # node_id -> site info
        self._pending: dict[str, tuple[int, float]] = {}  # query msg id -> (node_id, sent)
        self._query_failed: dict[int, float] = {}  # node_id -> retry-after time
        self._superseded: set[int] = set()  # nodes replaced by a newer node of the same site
        self._last_refresh = float("-inf")

    # ---------------------------------------------------------------- adapter I/O
    def _post(self, path: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
        """POST JSON to the adapter, retrying until ADAPTER_LOST_S has passed."""
        body = json.dumps(payload).encode("utf-8")
        started = time.monotonic()
        delay = 1.0
        while True:
            req = urllib.request.Request(
                self.adapter_url + path,
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except (urllib.error.URLError, ConnectionError, TimeoutError) as exc:
                if time.monotonic() - started > ADAPTER_LOST_S:
                    raise AdapterGone(f"adapter {self.adapter_url} unreachable: {exc}")
                log.warning("adapter %s%s unreachable (%s), retrying", self.adapter_url, path, exc)
                time.sleep(delay)
                delay = min(delay * 2, 10.0)

    def _node_list(self) -> list[dict[str, Any]]:
        return [{"node_id": nid, **info} for nid, info in sorted(self.sites.items())]

    # ---------------------------------------------------------------- node mapping
    def refresh_nodes(self, force: bool = False) -> None:
        """Track online nodes and ask new ones which site they serve (never blocks).

        Site-info QUERY messages are pushed once; their replies are collected on later
        calls, so a node that never answers cannot stall the relay loop.
        """
        now = time.monotonic()
        if self._pending:
            for reply in self.grid.pull_messages(list(self._pending)):
                nid, _ = self._pending.pop(reply.metadata.reply_to_message_id, (None, 0.0))
                if nid is not None:
                    self._accept_site_info(nid, reply)
            for mid, (nid, sent) in list(self._pending.items()):
                if now - sent > self.query_timeout_s:
                    log.warning("site-info query to node %s: no reply in %.0f s", nid, now - sent)
                    del self._pending[mid]
                    self._query_failed[nid] = now + QUERY_RETRY_S
        if not force and now - self._last_refresh < NODE_REFRESH_S:
            return
        self._last_refresh = now
        online = set(self.grid.get_node_ids())
        for nid in list(self.sites):
            if nid not in online:
                log.info("node %s (%s) went offline", nid, self.sites[nid]["site_id"])
                del self.sites[nid]
        self._superseded &= online
        asked = {nid for nid, _ in self._pending.values()}
        new = [
            nid
            for nid in sorted(online - set(self.sites) - asked - self._superseded)
            if self._query_failed.get(nid, float("-inf")) <= now
        ]
        if not new:
            return
        msgs = [
            Message(
                RecordDict({CONFIG: ConfigRecord({"query": QUERY_SITE_INFO})}),
                nid,
                MessageType.QUERY,
                ttl=self.query_timeout_s,
                group_id=QUERY_SITE_INFO,
            )
            for nid in new
        ]
        for nid, mid in zip(new, self.grid.push_messages(msgs), strict=False):
            if mid:
                self._pending[mid] = (nid, now)

    def _accept_site_info(self, nid: int, reply: Message) -> None:
        if reply.has_error() or SITE_INFO not in reply.content:
            log.warning("site-info query to node %s failed: %s", nid, _error_text(reply))
            self._query_failed[nid] = time.monotonic() + QUERY_RETRY_S
            return
        info = reply.content[SITE_INFO]
        site = str(info["site-id"])
        for other, other_info in list(self.sites.items()):
            if other != nid and other_info["site_id"] == site:
                # A restarted SuperNode gets a new id while the old id is still listed
                # until its heartbeat expires: the node that answered last wins.
                log.warning("site %s moved from node %s to node %s", site, other, nid)
                del self.sites[other]
                self._superseded.add(other)
        self.sites[nid] = {"site_id": site,
                           "client_code_version": str(info.get("client-code-version", "unknown"))}
        self._query_failed.pop(nid, None)
        log.info("node %s serves site %s", nid, site)

    def wait_for_sites(self, sites: list[str], timeout_s: float) -> None:
        """Give pending site-info queries a moment when a job needs an unmapped site."""
        end = time.monotonic() + timeout_s
        self.refresh_nodes(force=True)
        while time.monotonic() < end:
            mapped = {info["site_id"] for info in self.sites.values()}
            if set(sites) <= mapped or not self._pending:
                return
            time.sleep(PULL_INTERVAL_S)
            self.refresh_nodes()

    def _exchange(self, msgs: list[Message], timeout_s: float, on_reply: Any = None,
                  keep_waiting: Any = None) -> tuple[dict[int, Message], set[int]]:
        """Push messages, pull replies until all arrived, the timeout expired or
        ``keep_waiting()`` returned False.

        Returns ``({dst_node_id: reply}, lost)``; ``lost`` are destination nodes that
        went offline before replying. ``on_reply(node_id, reply)`` runs per reply.
        """
        if not msgs:
            return {}, set()
        ids = list(self.grid.push_messages(msgs))
        if len(ids) != len(msgs) or not all(ids):
            raise RuntimeError(f"SuperLink accepted {sum(map(bool, ids))} of {len(msgs)} messages")
        node_of = {mid: msg.metadata.dst_node_id for msg, mid in zip(msgs, ids, strict=True)}
        replies: dict[int, Message] = {}
        lost: set[int] = set()
        end = time.monotonic() + timeout_s
        next_check = time.monotonic() + NODE_REFRESH_S
        while node_of and time.monotonic() < end:
            for reply in self.grid.pull_messages(list(node_of)):
                nid = node_of.pop(reply.metadata.reply_to_message_id, None)
                if nid is None:
                    continue
                replies[nid] = reply
                if on_reply is not None:
                    on_reply(nid, reply)
            if not node_of or (keep_waiting is not None and not keep_waiting()):
                break
            if time.monotonic() >= next_check:
                online = set(self.grid.get_node_ids())
                for mid, nid in list(node_of.items()):
                    if nid not in online:
                        del node_of[mid]
                        lost.add(nid)
                next_check = time.monotonic() + NODE_REFRESH_S
            time.sleep(PULL_INTERVAL_S)
        return replies, lost

    # ---------------------------------------------------------------- training round
    def run_job(self, job: dict[str, Any]) -> None:
        """Run one dispatched round and report every site's outcome."""
        task_id = job["task_id"]
        state = {"cancel": False, "last_post": time.monotonic()}

        def report(results: list[dict[str, Any]], errors: list[dict[str, Any]], done: bool = False) -> None:
            res = self._post(
                "/relay/result",
                {"relay_id": self.relay_id, "task_id": task_id, "results": results,
                 "errors": errors, "done": done},
                timeout=30.0,
            )
            state["cancel"] = bool(res.get("cancel"))
            state["last_post"] = time.monotonic()

        try:
            blob = _http_get(job["model"]["url"], timeout=60.0)
            ref = sha256_ref(blob)
            if ref != job["model"]["ref"]:
                raise ValueError(f"model blob hash {ref} != {job['model']['ref']}")
            arrays = weights_to_arrayrecord(from_safetensors(blob))
        except Exception as exc:  # report, keep relay alive
            log.exception("task %s: cannot load global model", task_id)
            report([], [{"site_id": s, "error": f"relay: cannot load global model: {exc}"}
                        for s in job["sites"]], done=True)
            return

        self.wait_for_sites(job["sites"], timeout_s=min(self.query_timeout_s, 30.0))
        node_of_site = {info["site_id"]: nid for nid, info in self.sites.items()}
        # The dispatch train_config travels unchanged (flhub_core.TrainConfig keys).
        config = ConfigRecord(
            {
                **job["train_config"],
                "round": int(job["round"]),
                "base-model": str(job["model"]["ref"]),
                "task-id": str(task_id),
            }
        )
        msgs, errors = [], []
        for site in job["sites"]:
            nid = node_of_site.get(site)
            if nid is None:
                errors.append({"site_id": site, "error": f"site {site} has no connected SuperNode"})
                continue
            msgs.append(
                Message(
                    RecordDict({ARRAYS: arrays, CONFIG: config}),
                    nid,
                    MessageType.TRAIN,
                    ttl=float(job["deadline_s"]),
                    group_id=str(job["round"]),
                )
            )
        if errors:
            report([], errors)

        site_of_node = {nid: site for site, nid in node_of_site.items()}

        def on_reply(nid: int, reply: Message) -> None:
            site = site_of_node[nid]
            try:
                report([self._result_from_reply(site, nid, reply, job)], [])
            except Exception as exc:  # malformed reply or blob store failure
                log.exception("task %s: bad reply from %s", task_id, site)
                report([], [{"site_id": site, "error": str(exc)}])

        def keep_waiting() -> bool:
            if time.monotonic() - state["last_post"] > HEARTBEAT_S:
                report([], [])
            return not state["cancel"]

        try:
            replies, lost = self._exchange(msgs, float(job["deadline_s"]), on_reply, keep_waiting)
        except AdapterGone:
            raise
        except Exception as exc:  # SuperLink/Grid failure: fail the round, keep relay alive
            log.exception("task %s: grid exchange failed", task_id)
            report([], [{"site_id": site_of_node[m.metadata.dst_node_id],
                         "error": f"flower grid error: {exc}"} for m in msgs], done=True)
            return
        final_errors = []
        for m in msgs:
            nid = m.metadata.dst_node_id
            if nid in lost:
                final_errors.append({"site_id": site_of_node[nid],
                                     "error": f"SuperNode {nid} went offline during the round"})
            elif nid not in replies and not state["cancel"]:
                final_errors.append({"site_id": site_of_node[nid], "error":
                                     f"no reply within deadline_s={job['deadline_s']}"})
        report([], final_errors, done=True)

    def _result_from_reply(self, site: str, nid: int, reply: Message, job: dict[str, Any]) -> dict[str, Any]:
        if reply.has_error():
            raise RuntimeError(f"flower error from {site}: {_error_text(reply)}")
        content = reply.content
        blob = to_safetensors(arrayrecord_to_weights(content[ARRAYS]))
        ref = sha256_ref(blob)
        _http_put(job["blob_base_url"].rstrip("/") + f"/blobs/{ref}", blob, timeout=60.0)
        metrics = dict(content[METRICS])
        num_examples = int(metrics.pop("num-examples"))
        info = content[SITE_INFO] if SITE_INFO in content else {}
        reported_site = str(info.get("site-id", site))
        if reported_site != site:
            raise RuntimeError(f"node {nid} replied as {reported_site}, expected {site}")
        return {
            "site_id": site,
            "node_id": nid,
            "weights": ref,
            "num_examples": num_examples,
            "metrics": {k: float(v) for k, v in metrics.items() if isinstance(v, (int, float))},
            "client_code_version": str(info.get("client-code-version", "")),
        }

    # ---------------------------------------------------------------- main loop
    def serve(self) -> None:
        log.info("relay %s polling %s", self.relay_id, self.adapter_url)
        while True:
            self.refresh_nodes()
            cmd = self._post(
                "/relay/next",
                {"relay_id": self.relay_id, "wait_s": self.poll_timeout_s, "nodes": self._node_list()},
                timeout=self.poll_timeout_s + 30.0,
            )
            kind = cmd.get("type")
            if kind == "shutdown":
                log.info("relay %s: shutdown requested (%s)", self.relay_id, cmd.get("reason", ""))
                return
            if kind == "train":
                self.run_job(cmd["job"])


def _error_text(reply: Message) -> str:
    if reply.has_error():
        return f"code={reply.error.code} reason={reply.error.reason}"
    return "reply without expected content"


def _http_get(url: str, timeout: float) -> bytes:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return resp.read()


def _http_put(url: str, data: bytes, timeout: float) -> None:
    req = urllib.request.Request(url, data=data, method="PUT",
                                 headers={"Content-Type": "application/octet-stream"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        resp.read()


@app.main()
def main(grid: Grid, context: Context) -> None:
    """Entry point of the relay run (started by the adapter with ``flwr run``)."""
    cfg = context.run_config
    relay = Relay(
        grid,
        adapter_url=str(cfg["adapter-url"]),
        relay_id=str(context.run_id),
        poll_timeout_s=float(cfg["poll-timeout-s"]),
        query_timeout_s=float(cfg["query-timeout-s"]),
    )
    try:
        relay.serve()
    except AdapterGone as exc:
        log.error("relay %s exits: %s", context.run_id, exc)

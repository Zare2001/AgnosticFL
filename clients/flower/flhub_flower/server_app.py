"""Hub relay ServerApp. Runs only on the central VM, never on a hospital.

One long-lived Flower run, started by the Flower adapter service with ``flwr run``. The
loop long-polls the adapter for the next dispatched round (adapters.common.RelayClient),
sends standard TRAIN messages with the global weights to the hospital SuperNodes through
the ``Grid`` and posts the replies back. Weights move between the relay and the hub core
by blob reference (PROTOCOL.md §4); the adapter builds the envelopes.

SuperNode ids change whenever a SuperNode restarts, so the relay maps node ids to site ids
by sending a QUERY message to every new node (answered by the ClientApp's ``@app.query()``
handler with its ``site-id`` node config) and reports the mapped sites for ``discover``.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from adapters.common.hubclient import HubError, http_request
from adapters.common.relay import RelayClient
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

NODE_REFRESH_S = 5.0  # how often online nodes are re-read from the SuperLink
QUERY_RETRY_S = 30.0  # back-off for nodes that did not answer the site-info query
PULL_INTERVAL_S = 1.0
HEARTBEAT_S = 10.0  # progress post + cancel check interval while a round runs
ADAPTER_LOST_S = 600.0  # exit if the adapter is unreachable this long


class Relay:
    """Moves flhub rounds between the adapter service and the Flower Grid."""

    def __init__(self, grid: Grid, client: RelayClient, poll_timeout_s: float = 10.0,
                 query_timeout_s: float = 60.0) -> None:
        self.grid = grid
        self.client = client
        self.poll_timeout_s = poll_timeout_s
        self.query_timeout_s = query_timeout_s
        self.sites: dict[int, dict[str, Any]] = {}  # node_id -> site info
        self._pending: dict[str, tuple[int, float]] = {}  # query msg id -> (node_id, sent)
        self._query_failed: dict[int, float] = {}  # node_id -> retry-after time
        self._superseded: set[int] = set()  # nodes replaced by a newer node of the same site
        self._last_refresh = float("-inf")

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

    def report_sites(self) -> None:
        self.client.report_sites({info["site_id"]: "online" for info in self.sites.values()})

    # ---------------------------------------------------------------- message exchange
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
        """Run one dispatched round; post all site results, or one error."""
        job_id, sites, deadline_s = job["job_id"], list(job["sites"]), float(job["deadline_s"])
        try:
            arrays = weights_to_arrayrecord(self.client.get_model(job))
        except Exception as exc:  # report, keep the relay alive
            log.exception("job %s: cannot load the global model", job_id)
            self._post_error(job_id, f"relay cannot load global model {job['model']['ref']}: {exc}")
            return

        self.wait_for_sites(sites, timeout_s=min(self.query_timeout_s, 30.0))
        node_of_site = {info["site_id"]: nid for nid, info in self.sites.items()}
        missing = [s for s in sites if s not in node_of_site]
        if missing:
            self._post_error(job_id, f"no connected SuperNode for site(s) {missing}")
            return
        # The job config (dispatch train_config + round) travels unchanged to flhub_core.train.
        config = ConfigRecord({**job["config"], "base-model": str(job["model"]["ref"]), "job-id": str(job_id)})
        msgs = [
            Message(RecordDict({ARRAYS: arrays, CONFIG: config}), node_of_site[site], MessageType.TRAIN,
                    ttl=deadline_s, group_id=str(job["round"]))
            for site in sites
        ]
        site_of_node = {node_of_site[s]: s for s in sites}
        results: list[dict[str, Any]] = []
        errors: list[str] = []
        state = {"beat": time.monotonic(), "live": True}

        def on_reply(nid: int, reply: Message) -> None:
            site = site_of_node[nid]
            try:
                results.append(self._result_from_reply(site, nid, reply))
            except Exception as exc:  # error reply, malformed reply or blob store failure
                log.warning("job %s: bad reply from %s: %s", job_id, site, exc)
                errors.append(f"{site}: {exc}")
                return
            self._post_progress(job_id, len(results))

        def keep_waiting() -> bool:
            if time.monotonic() - state["beat"] >= HEARTBEAT_S:
                state["beat"] = time.monotonic()
                state["live"] = self._job_pending(job_id)
                if state["live"]:
                    self._post_progress(job_id, len(results))
            return state["live"]

        try:
            replies, lost = self._exchange(msgs, deadline_s, on_reply, keep_waiting)
        except Exception as exc:  # SuperLink/Grid failure: fail the round, keep the relay alive
            log.exception("job %s: grid exchange failed", job_id)
            self._post_error(job_id, f"flower grid error: {exc}")
            return
        if not state["live"]:
            log.info("job %s is no longer pending (cancelled or timed out)", job_id)
            return
        errors += [f"{site_of_node[n]}: SuperNode {n} went offline during the round" for n in lost]
        errors += [f"{site_of_node[n]}: no reply within {deadline_s:.0f} s" for n in site_of_node
                   if n not in replies and n not in lost]
        if errors:
            self._post_error(job_id, "; ".join(errors))
        else:
            self._post(lambda: self.client.post_result(job_id, results), job_id)

    def _result_from_reply(self, site: str, nid: int, reply: Message) -> dict[str, Any]:
        if reply.has_error():
            raise RuntimeError(f"flower error: {_error_text(reply)}")
        content = reply.content
        info = content[SITE_INFO] if SITE_INFO in content else {}
        reported = str(info.get("site-id", site))
        if reported != site:
            raise RuntimeError(f"node {nid} replied as {reported}, expected {site}")
        metrics = dict(content[METRICS])
        num_examples = int(metrics.pop("num-examples"))
        return self.client.result(site, arrayrecord_to_weights(content[ARRAYS]), num_examples, metrics,
                                  str(info.get("client-code-version", "unknown")))

    def _job_pending(self, job_id: str) -> bool:
        """False once the adapter dropped the job (cancelled or deadline exceeded)."""
        try:
            status, body = http_request(f"{self.client.base}/relay/status", "GET", timeout=10)
            return status != 200 or job_id in json.loads(body).get("pending", [job_id])
        except Exception:  # adapter briefly unreachable: keep waiting
            return True

    def _post(self, fn: Any, job_id: str) -> None:
        try:
            fn()
        except HubError as exc:  # e.g. 404 after a cancel: the adapter no longer wants it
            log.warning("job %s: post ignored by the adapter: %s", job_id, exc)

    def _post_error(self, job_id: str, message: str) -> None:
        self._post(lambda: self.client.post_error(job_id, message), job_id)

    def _post_progress(self, job_id: str, done: int) -> None:
        self._post(lambda: self.client.post_progress(job_id, done), job_id)

    # ---------------------------------------------------------------- main loop
    def serve(self) -> None:
        log.info("relay polling %s", self.client.base)
        last_ok = time.monotonic()
        while True:
            self.refresh_nodes()
            try:
                self.report_sites()
            except HubError as exc:
                log.warning("cannot report sites: %s", exc)
            job = self.client.next_job(timeout=self.poll_timeout_s)
            if job.get("error"):
                if time.monotonic() - last_ok > ADAPTER_LOST_S:
                    log.error("adapter unreachable for %.0f s, relay exits", ADAPTER_LOST_S)
                    return
                continue
            last_ok = time.monotonic()
            if job["type"] == "shutdown":
                log.info("relay: shutdown requested by the adapter")
                return
            if job["type"] == "train":
                self.run_job(job)


def _error_text(reply: Message) -> str:
    if reply.has_error():
        return f"code={reply.error.code} reason={reply.error.reason}"
    return "reply without the expected records"


@app.main()
def main(grid: Grid, context: Context) -> None:
    """Entry point of the relay run (started by the adapter with ``flwr run``)."""
    cfg = context.run_config
    client = RelayClient(str(cfg["adapter-url"]), hub_url=str(cfg["hub-url"]))
    Relay(grid, client, poll_timeout_s=float(cfg["poll-timeout-s"]),
          query_timeout_s=float(cfg["query-timeout-s"])).serve()

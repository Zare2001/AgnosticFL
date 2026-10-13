"""Hub relay ServerApp. Runs only on the central VM, never on a hospital.

One long-lived Flower run, started by the Flower adapter service with ``flwr run``. The
loop long-polls the adapter for the next dispatched round (adapters.common.RelayClient),
sends standard TRAIN messages with the global weights to the hospital SuperNodes through
the ``Grid`` and reports every site's reply back as soon as it arrives (flhub/0.2 per-site
results). Weights move between the relay and the hub core by blob reference (PROTOCOL.md
§4); the adapter builds the envelopes.

SuperNode ids change whenever a SuperNode restarts, so the relay maps node ids to site ids
by sending a QUERY message to every new node (answered by the ClientApp's ``@app.query()``
handler with its ``site-id`` node config) and reports the mapped sites for ``discover``.
A beat thread tells the adapter that this relay is alive, so a relay that died with its
SuperLink is replaced quickly (adapters/flower/service.py).
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any

from adapters.common.hubclient import HubError, http_request
from adapters.common.relay import RelayClient
from flhub_core import code_version
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
SITE_WAIT_S = 60.0  # a site without a SuperNode gets this long (<= 1/4 of the deadline) to connect
PULL_INTERVAL_S = 1.0
HEARTBEAT_S = 5.0  # how often a running round asks the adapter which sites it still wants
BEAT_S = 5.0  # liveness beat to the adapter, from its own thread (also during long transfers)
ADAPTER_LOST_S = 600.0  # exit if the adapter is unreachable this long
ACTIVE = ("queued", "running")


class Relay:
    """Moves flhub rounds between the adapter service and the Flower Grid."""

    def __init__(self, grid: Grid, client: RelayClient, poll_timeout_s: float = 10.0,
                 query_timeout_s: float = 60.0, fab_id: str = "", run_id: int | None = None) -> None:
        self.grid = grid
        self.client = client
        self.poll_timeout_s = poll_timeout_s
        self.query_timeout_s = query_timeout_s
        self.fab_id = fab_id
        self.run_id = run_id
        self.job_id: str | None = None  # round in progress, for the beat
        self._leave = threading.Event()  # the adapter serves another FAB: this relay must exit
        self.sites: dict[int, dict[str, Any]] = {}  # node_id -> site info
        self._pending: dict[str, tuple[int, float]] = {}  # query msg id -> (node_id, sent)
        self._query_failed: dict[int, float] = {}  # node_id -> retry-after time
        self._superseded: set[int] = set()  # nodes replaced by a newer node of the same site
        self._orphans: dict[str, tuple[str, str, float]] = {}  # msg id -> (site, job, forget-after)
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

    def node_of_site(self) -> dict[str, int]:
        return {info["site_id"]: nid for nid, info in self.sites.items()}

    def report_sites(self) -> None:
        self.client.report_sites({info["site_id"]: "online" for info in list(self.sites.values())})

    # ---------------------------------------------------------------- training round
    def run_job(self, job: dict[str, Any]) -> None:
        """Run one dispatched round and report each site as soon as it replies or fails.

        Sites without a connected SuperNode get up to SITE_WAIT_S (at most a quarter of the
        deadline) to (re)connect while the others already train. Sites the adapter no longer
        wants (cancelled per site, task over) are dropped; their late replies are discarded.
        Each site result carries the relay's timings (RQ4 round-time breakdown), added to the
        client metrics: flower_relay_prep_s (job -> model ready), flower_relay_push_s (TRAIN
        message pushed to the SuperLink), flower_relay_reply_s (pushed -> reply pulled: SuperNode
        download, ClientApp, upload) and flower_relay_store_s (reply -> weights in the hub core).
        """
        job_id, sites, deadline_s = job["job_id"], list(job["sites"]), float(job["deadline_s"])
        start = time.monotonic()
        end = start + deadline_s
        try:
            arrays = weights_to_arrayrecord(self.client.get_model(job))
            prep_s = time.monotonic() - start
        except Exception as exc:  # report, keep the relay alive
            log.exception("job %s: cannot load the global model", job_id)
            for site in sites:
                self._site_error(job_id, site, f"relay cannot load global model {job['model']['ref']}: {exc}")
            return
        # The job config (dispatch train_config + round) travels unchanged to flhub_core.train.
        config = ConfigRecord({**job["config"], "base-model": str(job["model"]["ref"]), "job-id": str(job_id)})
        give_up = start + min(SITE_WAIT_S, deadline_s / 4)
        waiting = list(sites)  # sites not sent yet (no connected SuperNode so far)
        sent: dict[str, tuple[int, str]] = {}  # message id -> (node id, site)
        timing: dict[str, dict[str, float]] = {}  # site -> relay timings
        next_check, next_ask = start + NODE_REFRESH_S, start + HEARTBEAT_S
        self.refresh_nodes(force=True)
        while (waiting or sent) and time.monotonic() < end:
            now = time.monotonic()
            if waiting:
                self.refresh_nodes()
                nodes = self.node_of_site()
                ready = [s for s in waiting if s in nodes]
                if ready:
                    t_push = time.monotonic()
                    sent.update(self._send(ready, nodes, arrays, config, end - now, job["round"]))
                    pushed = time.monotonic()
                    for site in ready:
                        timing[site] = {"flower_relay_prep_s": prep_s, "flower_relay_push_s": pushed - t_push,
                                        "pushed": pushed}
                    waiting = [s for s in waiting if s not in ready]
                if now >= give_up:
                    for site in waiting:
                        self._site_error(job_id, site, f"no connected SuperNode for site {site} "
                                                       f"after {now - start:.0f} s")
                    waiting = []
            replies = self.grid.pull_messages(list(sent)) if sent else []
            pulled = time.monotonic()
            for reply in replies:
                entry = sent.pop(reply.metadata.reply_to_message_id, None)
                if entry is not None:
                    t = timing.get(entry[1], {})
                    t["flower_relay_reply_s"] = pulled - t.pop("pushed", pulled)
                    self._report_reply(job_id, entry[1], entry[0], reply, t)
            if now >= next_check and (waiting or sent):
                next_check = now + NODE_REFRESH_S
                # A node that went offline, or whose site answered from a newer node (the
                # SuperNode restarted), will never reply.
                self.refresh_nodes(force=True)
                for mid, (nid, site) in list(sent.items()):
                    if nid not in self.sites:
                        del sent[mid]
                        self._site_error(job_id, site, f"SuperNode {nid} went offline or was replaced "
                                                       f"by a restarted SuperNode during the round")
            if now >= next_ask and (waiting or sent):
                next_ask = now + HEARTBEAT_S
                wanted = self._wanted_sites(job_id)
                if wanted is not None:  # the adapter's view: drop sites it no longer waits for
                    dropped = [s for s in waiting if s not in wanted]
                    waiting = [s for s in waiting if s in wanted]
                    for mid, (_, site) in list(sent.items()):
                        if site not in wanted:
                            del sent[mid]
                            self._orphan(mid, site, job_id, end)
                            dropped.append(site)
                    if dropped:
                        log.info("job %s: adapter no longer waits for %s", job_id, dropped)
            if waiting or sent:
                time.sleep(PULL_INTERVAL_S)
        for mid, (_, site) in sent.items():  # deadline passed: the adapter has failed these sites
            self._orphan(mid, site, job_id, end + 60.0)

    def _send(self, sites: list[str], nodes: dict[str, int], arrays, config: ConfigRecord, ttl: float,
              rnd: int) -> dict[str, tuple[int, str]]:
        msgs = [Message(RecordDict({ARRAYS: arrays, CONFIG: config}), nodes[site], MessageType.TRAIN,
                        ttl=max(ttl, 1.0), group_id=str(rnd)) for site in sites]
        ids = list(self.grid.push_messages(msgs))
        if len(ids) != len(msgs) or not all(ids):
            raise RuntimeError(f"SuperLink accepted {sum(map(bool, ids))} of {len(msgs)} messages")
        return {mid: (nodes[site], site) for mid, site in zip(ids, sites, strict=True)}

    def _report_reply(self, job_id: str, site: str, nid: int, reply: Message,
                      timing: dict[str, float] | None = None) -> None:
        try:
            t0 = time.monotonic()
            result = self._result_from_reply(site, nid, reply)
            result["metrics"].update({**(timing or {}), "flower_relay_store_s": time.monotonic() - t0})
        except Exception as exc:  # error reply, malformed reply or blob store failure
            log.warning("job %s: bad reply from %s: %s", job_id, site, exc)
            self._site_error(job_id, site, str(exc))
            return
        try:
            if not self.client.post_site_result(job_id, result):
                log.info("job %s: adapter dropped the late result of %s", job_id, site)
        except HubError as exc:
            log.warning("job %s: result of %s not delivered: %s", job_id, site, exc)

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

    def _wanted_sites(self, job_id: str) -> set[str] | None:
        """Sites the adapter still waits for; None if it cannot tell right now."""
        try:
            status = self.client.job_status(job_id)
        except Exception:  # adapter briefly unreachable: keep waiting
            return None
        if status is None or status.get("state") not in ACTIVE:
            return set()
        return {s for s, st in status.get("sites", {}).items() if st in ACTIVE}

    def _site_error(self, job_id: str, site: str, message: str) -> None:
        try:
            self.client.post_site_error(job_id, site, message)
        except HubError as exc:  # e.g. the job is gone after a cancel
            log.warning("job %s: error of %s not delivered: %s", job_id, site, exc)

    def _orphan(self, mid: str, site: str, job_id: str, until: float) -> None:
        """Keep pulling a reply nobody waits for, so it does not linger in the SuperLink."""
        self._orphans[mid] = (site, job_id, until)

    def drop_orphans(self) -> None:
        if not self._orphans:
            return
        for reply in self.grid.pull_messages(list(self._orphans)):
            site, job_id, _ = self._orphans.pop(reply.metadata.reply_to_message_id, ("?", "?", 0.0))
            log.info("job %s: dropped the late reply of %s (site no longer wanted)", job_id, site)
        now = time.monotonic()
        for mid, (_, _, until) in list(self._orphans.items()):
            if now > until:
                del self._orphans[mid]

    # ---------------------------------------------------------------- main loop
    def beat_forever(self) -> None:
        """Tell the adapter this relay is alive, independent of what the main loop does."""
        url = f"{self.client.base}/relay/beat"
        while True:
            body = {"fab_id": self.fab_id, "run_id": self.run_id, "job_id": self.job_id,
                    "code_version": code_version()}
            try:
                status, reply = http_request(url, "POST", json.dumps(body).encode(), "application/json", timeout=10)
                if status == 200 and json.loads(reply).get("leave"):
                    self._leave.set()
                    return
                self.report_sites()
            except Exception:  # adapter briefly unreachable, or an adapter without /relay/beat
                pass
            time.sleep(BEAT_S)

    def next_job(self) -> dict:
        """RelayClient.next_job plus this relay's fab id, so a superseded relay is told to leave."""
        url = f"{self.client.base}/relay/next?timeout={self.poll_timeout_s}&fab={self.fab_id}"
        try:
            status, body = http_request(url, "GET", timeout=self.poll_timeout_s + 30)
        except HubError as exc:
            time.sleep(2)
            return {"type": "idle", "error": str(exc)}
        if status != 200:
            time.sleep(2)
            return {"type": "idle", "error": f"HTTP {status}"}
        return json.loads(body)

    def serve(self) -> None:
        log.info("relay polling %s (fab %s)", self.client.base, self.fab_id or "-")
        threading.Thread(target=self.beat_forever, name="relay-beat", daemon=True).start()
        last_ok = time.monotonic()
        while True:
            if self._leave.is_set():
                log.info("relay: the adapter serves another FAB, this relay (fab %s) exits", self.fab_id)
                return
            self.refresh_nodes()
            self.drop_orphans()
            job = self.next_job()
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
                self.job_id = job["job_id"]
                try:
                    self.run_job(job)
                except Exception as exc:  # SuperLink/Grid failure: fail the sites, keep the relay alive
                    log.exception("job %s: grid exchange failed", job["job_id"])
                    for site in job["sites"]:
                        self._site_error(job["job_id"], site, f"flower grid error: {exc}")
                finally:
                    self.job_id = None


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
          query_timeout_s=float(cfg["query-timeout-s"]), fab_id=str(cfg.get("fab-id", "")),
          run_id=context.run_id).serve()

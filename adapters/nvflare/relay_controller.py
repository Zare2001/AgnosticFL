"""NVFlare server workflow that relays hub rounds to the NVFlare clients.

Runs inside the NVFlare server job "flhub-relay" (one long-running job). It long-polls the
NVFlare adapter service with adapters.common.RelayClient, sends the global model to the
requested clients with the standard controller API (one FULL-weights FLModel task per site, send())
and hands each client's returned weights to the adapter as a hub-core blob as soon as that
client replied (flhub/0.2 per-site results). A site that the hub no longer waits for
(cancelled, failed, deadline) gets its NVFlare task cancelled; its late reply is dropped.
"""

import functools
import math
import queue
import time

from nvflare.apis.controller_spec import SendOrder
from nvflare.apis.fl_constant import FLMetaKey, ReturnCode
from nvflare.app_common.abstract.fl_model import FLModel, ParamsType
from nvflare.app_common.workflows.model_controller import ModelController

from adapters.common.hubclient import HubError
from adapters.common.relay import RelayClient

# FLModel.meta keys shared with clients/nvflare/client.py
META_TRAIN_CONFIG = "train_config"
META_NUM_EXAMPLES = "num_examples"
META_CODE_VERSION = "client_code_version"
SITE_REPORT_S = 10.0  # site report interval while a round runs


class RelayError(Exception):
    pass


class HubRelayController(ModelController):
    def __init__(
        self,
        adapter_url: str = "http://127.0.0.1:8811",
        hub_url: str = None,
        poll_timeout_s: float = 20.0,
        cancel_poll_s: float = 1.0,
        task_name: str = "train",
    ):
        """Relay workflow: hub job -> one NVFlare task per site -> each client's weights back to the adapter.

        Args:
            adapter_url: NVFlare adapter service (localhost only).
            hub_url: hub core URL; None = adapters.common default (127.0.0.1:8800 / $FLHUB_HUB_URL).
            poll_timeout_s: long-poll duration for the next hub job.
            cancel_poll_s: how often a running round asks the adapter which sites it still waits for.
            task_name: NVFlare task name sent to the clients.
        """
        # no persistor: the hub owns the global model; client errors must not end the job
        super().__init__(persistor_id="", ignore_result_error=True)
        self.adapter_url = adapter_url
        self.hub_url = hub_url
        self.poll_timeout_s = poll_timeout_s
        self.cancel_poll_s = cancel_poll_s
        self.task_name = task_name

    def _job_clients(self) -> dict:
        """{client name: online|offline} for the clients deployed in this job."""
        names = sorted(c.name for c in self.engine.get_clients())
        return {n: "offline" if self.get_client_disconnect_time(n) else "online" for n in names}

    def run(self):
        relay = RelayClient(self.adapter_url, self.hub_url)
        self.info(f"relay started in job {self.fl_ctx.get_job_id()}; adapter {self.adapter_url}")
        unreachable_since = None
        while not self.abort_signal.triggered:
            try:
                relay.report_sites(self._job_clients())
            except HubError:
                pass  # next_job() below waits and reports the problem
            job = relay.next_job(timeout=self.poll_timeout_s)
            if job.get("error"):
                if unreachable_since is None:
                    unreachable_since = time.monotonic()
                    self.warning(f"adapter not reachable: {job['error']}; retrying")
                continue
            if unreachable_since is not None:
                self.info(f"adapter reachable again after {time.monotonic() - unreachable_since:.0f}s")
                unreachable_since = None
            if job["type"] == "shutdown":
                self.info("shutdown requested by the adapter; ending the relay job")
                return
            if job["type"] == "train":
                self._train(relay, job)
        self.info("abort signal received; ending the relay job")

    # ---- one hub round ---------------------------------------------------------------------
    def _train(self, relay: RelayClient, job: dict):
        job_id, rnd = job["job_id"], int(job["round"])
        clients = self._job_clients()
        sites = []
        for site in job["sites"]:
            if clients.get(site) == "online":
                sites.append(site)
            else:
                self._site_error(relay, job_id, site, f"{site} is not online in the NVFlare relay job "
                                                      f"(job clients: {clients})")
        if not sites:
            return
        try:
            params = relay.get_model(job)
        except Exception as e:
            for site in sites:
                self._site_error(relay, job_id, site, f"cannot load the global model: {type(e).__name__}: {e}")
            return
        model = FLModel(params_type=ParamsType.FULL, params=params, current_round=rnd,
                        meta={META_TRAIN_CONFIG: dict(job["config"])})
        timeout = max(1, math.ceil(float(job.get("deadline_s") or 0)))
        inbox = queue.Queue()  # (site, FLModel | None, error | None), filled by NVFlare's result threads
        self.set_fl_context(model)
        tasks = {}
        for site in sites:  # one task per site, so that each site can be cancelled on its own
            task = self._prepare_task(data=model, task_name=self.task_name, timeout=timeout,
                                      callback=functools.partial(self._on_reply, inbox, site))
            task.result_received_cb = functools.partial(self._on_result, inbox, site, task.result_received_cb)
            # send(), not broadcast(): a broadcast task deep-copies the model once per task
            self.send(task=task, fl_ctx=self.fl_ctx, targets=[site], send_order=SendOrder.ANY)
            tasks[site] = task
        self.info(f"job {job_id}: round {rnd} sent to {sites} (deadline {timeout}s)")
        t0, settled, next_poll, next_report = time.monotonic(), set(), 0.0, time.monotonic() + SITE_REPORT_S
        while True:
            self._drain(relay, job_id, inbox, settled, t0)
            standing = {s: t for s, t in tasks.items() if t.completion_status is None}
            if not standing:
                break
            if self.abort_signal.triggered:
                for t in standing.values():
                    self.cancel_task(t)
                break
            if time.monotonic() >= next_poll:
                next_poll = time.monotonic() + self.cancel_poll_s
                self._cancel_unwanted(relay, job_id, standing)
            if time.monotonic() >= next_report:  # keep the adapter's site view fresh during long rounds
                next_report = time.monotonic() + SITE_REPORT_S
                try:
                    relay.report_sites(self._job_clients())
                except HubError:
                    pass
            time.sleep(0.1)
        self._drain(relay, job_id, inbox, settled, t0)
        for site, t in tasks.items():
            if site not in settled:
                self._site_error(relay, job_id, site, f"no result from {site}: NVFlare task ended with "
                                                      f"{t.completion_status}")
        self.info(f"job {job_id}: round {rnd} done in {time.monotonic() - t0:.1f}s")

    @staticmethod
    def _on_result(inbox: queue.Queue, site: str, inner, client_task, fl_ctx):
        """Raw result hook: a client error reaches the hub at once (ModelController drops it silently)."""
        result = client_task.result
        rc = result.get_return_code() if result is not None else None
        if rc and rc != ReturnCode.OK:
            inbox.put((site, None, f"{site} replied with NVFlare return code {rc}"))
        inner(client_task=client_task, fl_ctx=fl_ctx)

    @staticmethod
    def _on_reply(inbox: queue.Queue, site: str, reply: FLModel):
        inbox.put((site, reply, None))
        return True

    def _drain(self, relay: RelayClient, job_id: str, inbox: queue.Queue, settled: set, t0: float):
        """Hands the replies received so far to the adapter (outside NVFlare's result threads)."""
        while True:
            try:
                site, reply, error = inbox.get_nowait()
            except queue.Empty:
                return
            if site in settled:
                continue
            settled.add(site)
            if error is not None:
                self._site_error(relay, job_id, site, error)
                continue
            try:
                if reply.meta.get("client_name") != site:
                    raise RelayError(f"reply for {site} came from {reply.meta.get('client_name')!r}")
                accepted = relay.post_site_result(job_id, self._to_result(relay, site, reply))
                self.info(f"job {job_id}: {site} replied after {time.monotonic() - t0:.1f}s"
                          + ("" if accepted else " (dropped by the adapter)"))
            except Exception as e:
                self._site_error(relay, job_id, site, f"{type(e).__name__}: {e}")

    def _cancel_unwanted(self, relay: RelayClient, job_id: str, standing: dict):
        """Cancels the NVFlare task of every site the hub no longer waits for."""
        try:
            status = relay.job_status(job_id)
        except (HubError, ValueError) as e:
            self.warning(f"job {job_id}: cannot read its status at the adapter: {e}")
            return
        sites = (status or {}).get("sites", {})
        for site, t in standing.items():
            if sites.get(site) != "running":
                self.info(f"job {job_id}: {site} is {sites.get(site, 'unknown to the adapter')}; "
                          f"cancelling its NVFlare task")
                self.cancel_task(t)

    def _site_error(self, relay: RelayClient, job_id: str, site: str, message: str):
        msg = f"NVFlare relay: {message}"
        self.error(f"job {job_id}: {msg}")
        try:
            relay.post_site_error(job_id, site, msg)
        except HubError as e:  # e.g. 404: the adapter forgot the job
            self.warning(f"could not report the failure of {site} in {job_id}: {e}")

    @staticmethod
    def _to_result(relay: RelayClient, site: str, reply: FLModel) -> dict:
        if reply.params_type != ParamsType.FULL or not reply.params:
            raise RelayError(f"{site} sent params_type {reply.params_type}; FULL weights are required")
        meta = reply.meta or {}
        n = meta.get(META_NUM_EXAMPLES, meta.get(FLMetaKey.NUM_STEPS_CURRENT_ROUND))
        if n is None or int(n) <= 0:
            raise RelayError(f"{site} sent no positive '{META_NUM_EXAMPLES}' in FLModel.meta")
        return relay.result(site, reply.params, int(n), reply.metrics, meta.get(META_CODE_VERSION, "unknown"))

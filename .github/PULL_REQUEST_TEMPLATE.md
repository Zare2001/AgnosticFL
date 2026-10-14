**What changes**

<!-- One or two sentences; link the issue. -->

**How it was tested**

- [ ] `python -m pytest` (unit tests)
- [ ] `python -m pytest tests/conformance --adapter mock`, and against a real deployment if an adapter changed
- [ ] runs still equal the simulation (`python -m hub.sim --config … --compare <run dir>`) if the orchestrator, an aggregator or `flhub_core` changed
- [ ] `python -m flhub_core.selfcheck` digest checked if `flhub_core` changed

**Documentation**

- [ ] documents updated for the change; numbers cite their evidence
- [ ] `python scripts/docs/check_links.py` passes
- [ ] no credentials, tokens or real host addresses in code, configs or documents

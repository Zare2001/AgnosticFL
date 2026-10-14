# <Framework> guide

Template for a framework guide. Copy it to `docs/frameworks/<framework>.md`, keep the eight sections in this order, and delete these instructions. Every number cites its evidence file under `docs/results/`; use placeholders such as `<central-host>` instead of real addresses.

## 1. Summary

- Tested framework version and status (tested in stage 1, stage 2, or both).
- What the adapter does, in two sentences.
- How new hospital-side code reaches the sites (the rollout channel).

## 2. Server side: how the adapter maps flhub onto <Framework>

- Components: the adapter service and, if used, the relay job, run or tasks inside the framework server.
- How one round is executed (`dispatch` → framework messages → envelopes).
- Ports, configuration, start, stop and health check.
- How sites are discovered and identified (`site_id`).

## 3. Client side: what the hospital code must contain

- Walk through the example in `clients/<framework>/` with short quoted snippets (the essential lines only).
- Mark what is the framework's standard pattern and what is specific to flhub (see [the client contract](../client-contract.md)).
- How the client gets its data and data variants.
- Packaging and rollout of new library versions.
- The hospital-side change ledger: code lines, configuration and operations steps.

## 4. Deploying next to an existing <Framework> federation

- Steps on the operator's side and at the hospitals, and the credentials involved.

## 5. Cost and efficiency for <Framework>

- Lines of code of the adapter and the client.
- Rollout steps, time and downtime.
- Overhead against the framework's own FedAvg.
- Fixed cost per round and cost per MiB (cost model).
- Bytes on the wire ÷ payload; memory per component; bandwidth efficiency.
- Scaling to more sites; fault detection and recovery times.
- Link the figures in `docs/results/figures/`.

## 6. Limits, known issues and knobs

- Measured limits, failure modes found, and which knobs are server-side and which hospital-side.

## 7. Troubleshooting

- Problems met during testing, as short entries: symptom → cause → fix.

## 8. Future work and upstream reports

- What would lift the limits.
- Draft texts for upstream issues where a framework bug was found (not filed).

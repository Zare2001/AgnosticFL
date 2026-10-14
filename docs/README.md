# Documentation

AgnosticFL runs one federated-learning server (the hub) across hospitals that use different frameworks: NVFlare, vantage6 and Flower. Start with [Getting started](getting-started.md); the pages below go deeper.

| Page | What it covers |
|---|---|
| [Getting started](getting-started.md) | Run the hub core, a mock adapter, the orchestrator and the simulation on one machine, then connect a real framework. |
| [Architecture](architecture.md) | Components, one round step by step, determinism, partial participation and resume, the trust and security model. |
| [Protocol](protocol.md) | `flhub/0.1` and `flhub/0.2`: the JSON-RPC protocol between the orchestrator and the adapters, and the hub core API. |
| [Client contract](client-contract.md) | What every hospital-side client must receive, run and return, independent of the framework. |
| [NVFlare](frameworks/nvflare.md), [vantage6](frameworks/vantage6.md), [Flower](frameworks/flower.md) | Per framework: the server-side adapter, the hospital-side code, deployment, cost, limits and troubleshooting. |
| [Adding a framework](adding-a-framework.md) | A new adapter: `BaseAdapter` or `RelayAdapter`, the conformance suite, a checklist and the measured effort. |
| [Aggregators](aggregators.md) | The six server aggregators, their parameters and tuned values, and how to add one. |
| [Deployment](deployment.md) | The hub and an adapter next to an existing framework server; the reference 4-VM testbed. |
| [Reproducing results](reproducing-results.md) | How `configs/`, `scripts/` and `deploy/` reproduce the stage-1 and stage-2 results. |
| [Support matrix](support-matrix.md) | Frameworks and tested versions, features, measured limits, what is not supported. |
| [Cost and efficiency](cost-and-efficiency.md) | What it cost to build and what it costs to use the framework-agnostic server. |
| [Limitations and future work](limitations-and-future-work.md) | Testbed and method limits, framework findings, prioritised future work. |
| [Design decisions](design/decisions.md) | The architecture decision records (ADR-001 to ADR-022). |
| [Results](results/README.md) | Headline numbers of both test stages, the stage summaries, figures and evidence. |

A new framework guide starts from [frameworks/_template.md](frameworks/_template.md).

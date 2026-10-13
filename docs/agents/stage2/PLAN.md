# AgnosticFL — Stage 2 plan: full system and performance test

## 0. Context and how to read this

Stage 1 is finished: the hub, the three adapters and the six hospitals run on the four VMs, `REPORT.md` describes them, git tag `stage1-final` marks the code, and W&B project `AgnosticFL` holds the six stage-1 final runs. **Stage 2 reuses that deployment. Do not rebuild it.**

Read in this order: this file → `EXPERIMENTS.md` (same folder) → your role file `docs/agents/stage2/roles/<ROLE>.md` → `REPORT.md` and the tail of your stage-1 worklog (what already exists and how it is started).

Still valid from stage 1, unless this file overrides them:

- `docs/agents/PROTOCOL.md` (flhub/0.1).
- `docs/agents/COLLABORATION.md`, covering the bus, git rules, push announcements, secrets, subagents, worklogs and never-stop.
- `docs/agents/PROJECT_PLAN.md` §2–§5 (topology, architecture, ownership, technical decisions) and §11 (fallbacks).

`PROJECT_PLAN.md` §6 (phases) and §10 (Definition of Done) are finished and no longer apply.

**Superseded stage-1 clauses.** Where these conflict with stage 1, stage 2 wins:

| Stage-1 clause | Replaced by |
|---|---|
| PROJECT_PLAN §5: model ≤ 2 M parameters / ≤ 8 MB | the model zoo in §4.2 |
| PROJECT_PLAN §11: "large payloads hit a framework limit: shrink the model" | §10 here: a limit is a result |
| PROTOCOL: "an adapter is ready only when all its sites are online"; `max_payload_mb: 64` | §4.4: a run needs only the sites in its own `sites` list to be online; caps are raised |
| COLLABORATION §6: final runs online from central, group `final-<date>` | §6 here: finalize-then-upload, groups `E1`–`E7` |
| COLLABORATION §8–§9 and the stage-1 CLAUDE.md: worklog path, Definition of Done | `docs/agents/worklogs/stage2-<role>.md`; §9 here |

Agent bus conventions for stage 2:

- Stage-2 messages are everything after the message whose subject starts with `STAGE2-START`.
- Prefix stage-2 milestone subjects with `S2 `.
- Ignore stage-1 `final-call` and `all-done` messages.

Worklog: `docs/agents/worklogs/stage2-<role>.md` (new file; the stage-1 worklog stays as history).

## 1. Mission and research questions

Stage 1 showed that the translation layer is lossless in the easiest setting (IID data, FedAvg, small CNN, six sites). Stage 2 tests the **whole system** under realistic and demanding conditions and measures its **performance**. Headline claim to confirm or refute:

> Changing the aggregation algorithm, the data distribution or the model is a hub-side change. Hospitals keep running the same standard client glue in their own framework, and the result equals a framework-free reference.

| RQ | Question | Main evidence |
|---|---|---|
| RQ1 Correctness | Does the real cross-framework system still equal the framework-free reference under non-IID and uneven data, other aggregators, larger models, partial participation and restarts? | INV-1 for every final run |
| RQ2 Hospital cost | What must change on the hospital side to switch aggregator, data or model, and how does each framework roll out a library update? | Hospital-side change ledger; `git diff stage1-final -- clients/` |
| RQ3 Learning | How do FedAvg, FedAvgM, FedAdam, FedYogi, FedAdagrad and FedProx compare under label skew, quantity skew and framework-correlated skew? Does mixing frameworks change anything? | E1, E2, E3 |
| RQ4 Performance | Where does round time go (training, transport, framework, hub)? What payload sizes does each framework carry, at what cost, and how does it scale with bandwidth and the number of sites? What is the overhead compared with each framework's native FedAvg? | E3, E4, E5, E7 |
| RQ5 Robustness | What happens with dropouts, stragglers, partial participation, and hub, adapter or framework-server restarts, especially with stateful server optimizers? | E6 |

## 2. Invariants (must hold for every final run; a violation needs an ADR and a line in the report)

- **INV-1 Equivalence.** Every final run on the real system equals `hub.sim` for the same config, seed and participants.
  - `metrics.jsonl` already records the sha256 of every round's global weights (`global_ref`). Stage 2 adds the sha256 of the aggregator state (`agg_state_ref`).
  - Pass: identical hashes in every round (diff 0.0, the expected case). Otherwise compute the max abs weight difference from the kept checkpoints; it must be ≤ 1e-5 in every round.
  - Use full sim where it fits the budget. Otherwise use the *replay spot-check*: recompute rounds 1, 2, 3 and the last round from the real run's previous global weights and aggregator state (§4.3).
  - Record the method and the max difference in the run's W&B summary (`inv1_method`, `inv1_max_diff`) and in `docs/report/stage2/runs.md`.
  - The gates for sim, reference and native runs are in §6.
- **INV-2 Hospital glue unchanged.** Target: 0 changed lines in `clients/` for every server-side feature (aggregators, partial participation, resume, models). Allowed:
  - one shared-library rollout (`flhub_core` 0.2.x) through each framework's normal code-distribution channel;
  - data provisioning;
  - connection or config changes.

  Every hospital-side change goes into the ledger (§4.6).
- **INV-3 Protocol.** flhub/0.1 stays valid. Extensions are backward compatible (new optional fields only) and are owned by central (ADR + conformance tests). `train_config` is already opaque to adapters; keep it that way.
- **INV-4 Reproducibility.** A final run is fully defined by (frozen git SHA, `configs/stage2/<run>.yaml`, partition manifest sha256). Re-running gives identical weights.
- **INV-5 W&B hygiene.**
  - Final stage-2 runs go only to project `AgnosticFL-stage2`, and only after their gate passed (§6).
  - Project `AgnosticFL` is read-only: never log to it or delete from it.
- **INV-6 Stage-1 results untouched.** Do not change `REPORT.md` except for one link line to the stage-2 report. Do not move or retag `stage1-final`.

## 3. Owner decisions for this stage (fixed)

- **Scope: "Full"**: experiments E1–E7 in `EXPERIMENTS.md` (MUST + SHOULD).
  - Out of scope, listed as future work in the report: SCAFFOLD and other control-variate methods, robust aggregators (median, trimmed mean, Krum), federated evaluation at the sites, secure aggregation, differential privacy, GPUs.
- **W&B project: `AgnosticFL-stage2`.** `start_stage2.sh` sets `WANDB_PROJECT=AgnosticFL-stage2` in `~/.flhub/secrets.env`.
- **Network shaping and fault injection are allowed**, only under the safety rules in §8.
- **The stage-1 assumptions still hold:** honest hospitals, correct code, no anomaly detection. Failures in E6 are injected on purpose and announced.

## 4. What changes where

### 4.1 Aggregators — `hub/aggregators/` (central)

A plugin registry of pure-numpy, framework-free, **stateful** server aggregators. One implementation is used by both `hub.orchestrator` and `hub.sim`, which is what keeps INV-1 at 0.0.

```
class Aggregator:
    name: str
    def init(self, global_weights, params) -> state            # state: dict[str, float64 ndarray] + scalars
    def aggregate(self, global_weights, results, state, round) -> (new_global_weights, new_state)
        # results: [(site_id, weights, num_examples, metrics)] of the sites that participated this round
```

With `p_k = n_k / Σ n_j` over the participating sites, and pseudo-gradient `Δ = Σ_k p_k (w_k − x)`:

| name | update (all arithmetic and state in float64; new global cast to float32 once per round) | defaults |
|---|---|---|
| `fedavg` | `x ← Σ_k p_k w_k`. **Keep the exact stage-1 code path.** The mathematically equal form `x + Δ` is not bit-equal. | — |
| `fedavgm` | `m ← β m + Δ;  x ← x + η m`  (Hsu et al. 2019) | β 0.9 |
| `fedadam` | `m ← β1 m + (1−β1) Δ;  v ← β2 v + (1−β2) Δ²;  x ← x + η m / (√v + τ)`  (Reddi et al. 2021) | β1 0.9, β2 0.99, τ 1e-3, v₀ = τ² |
| `fedyogi` | as fedadam but `v ← v − (1−β2) Δ² sign(v − Δ²)` | as fedadam |
| `fedadagrad` | `v ← v + Δ²;  x ← x + η Δ / (√v + τ)` (no momentum) | τ 1e-3, v₀ = τ² |
| `fedprox` | server = `fedavg`; clients minimize `f(w) + (μ/2)‖w − x‖²`, with μ from `train_config.prox_mu` (in `flhub_core`; μ = 0 must skip the term and be bit-equal to plain SGD) | μ from S3 |

Server learning rates η, μ and the client learning rate are tuned in S3 (`EXPERIMENTS.md`).

Requirements:

- The state is checkpointed after every round, next to the global weights, so a run can resume (E6d).
- Unit tests:
  - Each aggregator against a hand-written formula on small random arrays.
  - `fedavg` against the stage-1 implementation (bit-exact).
  - All aggregators with one site and Δ = 0 (no change).

### 4.2 `flhub_core` 0.2 (central)

- **Datasets.**
  - Keep `pneumoniamnist`, used for the regression test.
  - Add one **multi-class** MedMNIST dataset (ADR) so that label skew is meaningful. Shortlist:
    - OrganAMNIST: 11 classes, ~35k training images; the recommended balance of size and speed.
    - BloodMNIST: 8 classes, ~12k; faster.
    - PathMNIST: 9 classes, ~90k; probably too slow on CPU.
  - Use 28 px by default. 64 px (MedMNIST+) is optional and only for E3.
  - Split the official training split into the site partitions. Keep the official validation split on central for S3 tuning. Use the official test split only for final evaluation.
- **Partitioners.** `iid`, `dirichlet(α)`, `quantity(ratios)`, `fwcorr` and `mix`, defined in `EXPERIMENTS.md`.
  - Deterministic from a separate `partition_seed` (2026). The training seeds 2026/2027/2028 do not change the partitions.
  - Each writes a manifest `configs/stage2/partitions/<dataset>-<pid>.json`: site → n, label histogram, sha256 of the materialized file. Commit the manifest; it is small JSON.
- **Model zoo.**

  | id | what | size |
  |---|---|---|
  | `cnn-small` | the stage-1 model; same parameter names and init | as stage 1 |
  | `cnn-medium` | wider CNN, no BatchNorm | ~1–3 M parameters |
  | `resnet18-gn` | ResNet-18, CIFAR-style stem, GroupNorm instead of BatchNorm | ~11 M parameters, ~45 MB float32 |
  | `payload-<MB>` | synthetic float32 tensors totalling `<MB>` MB, for transport tests (E4, E7) | as named |

  `payload-<MB>` "trains" with a deterministic echo, `w + 1e-3 · g(seed, round, data fingerprint)`, with no compute, so round time is pure transport and framework overhead.

  No BatchNorm anywhere: the float32-only weights spec has no integer buffers.
- **Training.**
  - FedProx `prox_mu`, optimizer options (momentum, weight decay) and `data_variant` (see "Data variants" below).
  - Torch threads stay at 1 per site unless an ADR proves bit-exactness with more.
  - The returned metrics add `train_time_s` (wall time of local training only) and `peak_rss_mb`. Metrics are opaque to adapters, so no adapter change is needed.
- **Data variants without code changes.** Each hospital provisions all stage-2 partition files once (S2). Choosing the variant for a run must not need a code change or a manual step per run. Preferred design (central decides, ADR):
  - The hospital's configured data path points to a **directory** holding `<dataset>-<pid>-<site>.csv`.
  - `load_site(dir)` returns a lazy handle that `train(weights, data, cfg)` resolves with `cfg["data_variant"]`.
  - The stage-1 call `train(weights, load_site(path), cfg)` stays unchanged.

  vantage6 receives a DataFrame from the node, so it needs labelled databases or an equivalent (vantage6 agent decides, ADR). Whatever is chosen goes into the ledger.
- **Backward compatibility.** `flhub_core` 0.2 with the stage-1 final config reproduces the round weights of the stage-1 `mixed-6sites` run bit-exactly. Test this against the stage-1 run directory under `~/flhub-runtime/runs/` on central.
- **Selfcheck v2.** One hash per model in the zoo. Run it on all four VMs, in the vantage6 image and in whatever the NVFlare job and the Flower FAB execute. All hashes must match before S2 ends.

### 4.3 Orchestrator and sim (central)

- **Config schema v2** (`configs/stage2/*.yaml`):
  - dataset, partition / `data_variant`, model, `aggregator: {name, params}`, `train_config`, rounds, seed, sites (any subset of the 6 or 12 sites);
  - `participation: {fraction, seed, min_sites}`, `deadline_s`, `round_retries`;
  - `eval: {every, site_shaped}`, `retention`, `wandb: {group, tags}`.
- **Participation.**
  - Seeded client sampling (`fraction < 1`).
  - `min_sites` lets a round finish without late or dead sites once the deadline passes. It needs the protocol extension in §4.4. The default is all sites, as in stage 1.
  - The participants of every round are recorded. `hub.sim --participants-from <run_dir>` replays them, so INV-1 stays defined.
- **Resume.** `--resume <run_dir>` restarts from the last completed round, including the aggregator state and the sampling RNG state. Continuing must be bit-exact (E6d).
- **No live W&B logging.** The orchestrator writes `metrics.jsonl` and `summary.json` only. `scripts/stage2/finalize_run.py` creates the W&B run afterwards (§6). A SIGKILL (E6d) therefore never leaves a half-finished W&B run.
- **Instrumentation per round:**
  - `t_dispatch` per adapter; per site the result time and the client-reported `train_time_s`;
  - aggregation time, eval time and total round time;
  - derived per site: `overhead_s = (t_result − t_dispatch) − train_time_s` (transport + framework + adapter);
  - `global_ref` and `agg_state_ref` (sha256).
- **System monitoring.** `scripts/sysmon.py` (central provides it) records CPU %, RSS of named processes and interface bytes every 1 s.
  - Every agent runs it **continuously** on its own VM during S4, into rotating local files.
  - Central broadcasts `info` "RUN start <name> <UTC>" and "RUN end <name> <UTC>" for every final run.
  - Each framework agent then cuts that window into a small JSON summary (peak and mean CPU and RSS per process, bytes in and out) and sends it with `tools/bus put <file> --to central`. Central stores it in the run directory.
- **Bytes on wire per framework and round.** Use the interface byte deltas on each framework VM. Each framework VM carries only that framework's traffic plus a small, measurable bus and SSH baseline. Do not use firewall counters (no iptables/nft changes; PLAN §8). Report the protocol overhead as bytes on wire ÷ payload bytes.
- **Simulation.**
  - `hub.sim --workers N` trains sites in parallel processes with a deterministic result order, so `resnet18-gn` stays feasible.
  - `hub.sim --replay <run_dir> --rounds 1,2,3,last` implements the replay spot-check.
- **Evaluation:**
  - global test loss, accuracy, balanced accuracy, macro one-vs-rest AUC;
  - **site-shaped accuracy**: the test set reweighted to each site's label histogram, giving per-site accuracy plus worst-site and standard deviation without any protocol change;
  - rounds-to-target (first round with test accuracy ≥ 0.95 × centralized accuracy).

### 4.4 Adapters (framework agents)

- Expected changes for E1–E3: none.
- **Protocol flhub/0.2: backward-compatible extensions.** Central specifies them in S1 (PROTOCOL.md, ADR). Framework agents implement them in their adapters **before S3-frozen**:
  - **Per-site progress:** `tasks/get` adds `sites: {site_id: queued|running|done|failed}`.
  - **Partial collect:** `collect {task_id, partial: true}` returns the envelopes of the sites that are done, even if the task is still running or has failed.
  - **Per-site cancel:** `cancel {task_id, sites: [...]}` stops the remaining sites. After a partial collect + cancel, the adapter must accept the next `dispatch` without `-32015`. Late results from cancelled sites are dropped and logged.
  - **Readiness per run:** a run needs only the sites in its own `sites` list to be online. Registered but offline sites (for example the E5 extras) must not block other runs, and `status.sh` health covers the six base sites.
  - **Payload cap:** `max_payload_mb` and any hub-side size caps are raised to ≥ 1024 MB before S3-frozen, so E4a measures framework limits and not our own cap.
  - **Self-healing after a framework-server restart (E6e):** the NVFlare relay job is resubmitted, the Flower relay run relaunched, and the vantage6 client reconnects, all automatically. Stage 1 already has keepers for some of this; verify and complete them.
- Other possible changes, each with an ADR, a ledger line if it touches the hospital side, and conformance still passing:
  - server-side message-size and streaming settings for large payloads (E4);
  - vantage6 database label (if chosen).
- **Conformance suite v2** (central adds the tests). New checks:
  - subset dispatch;
  - partial collect + per-site cancel, then an immediate next dispatch;
  - a registered but offline site does not block a dispatch to the others;
  - one `payload-64` round (echo);
  - the adapter survives its own restart between rounds.

  Every adapter must pass v2 against central before S3-frozen.

### 4.5 Hospitals (framework agents)

- **Roll out `flhub_core` 0.2** preferably through the framework's own code-distribution channel, so hospitals need **zero manual steps**:
  - NVFlare: the job's custom code;
  - Flower: the FAB;
  - vantage6: a new algorithm image tag.

  If that is impossible, use a one-time scripted install. Measure and report per framework: steps, wall time, client restarts, downtime, and lines changed in `clients/<fw>/`. This comparison is itself a result (RQ2).
- **Provision data:** materialize all stage-2 data variants for your sites once, sha256-verified against the manifests.
- **E5 extra sites:** two more per VM (`nvf-h7`, `nvf-h8`, `v6-h9`, `v6-h10`, `flw-h11`, `flw-h12`), started with the same scripts (central provisions credentials). They run only during E5. Being registered but offline must not affect other runs (§4.4).

### 4.6 Hospital-side change ledger

`docs/report/stage2/ledger.md` lists every hospital-side change in stage 2, with these columns: UTC time, framework, type (code | config | data | ops), what, why, lines changed, restart needed, ADR.

- Each framework agent maintains the rows for its own framework.
- The file must exist even if a framework has no rows.
- At the end, central adds the output of `git diff --stat stage1-final..HEAD -- clients/`.

## 5. Phases and milestones

Announce milestones with `tools/bus send --to all --type milestone --subject "S2 <ID> <one line>"` and update `tools/bus status-set --phase S<n>`.

| Phase | Who | Content | Milestone |
|---|---|---|---|
| **S0 Kickoff** (≤ 1 h) | all | Pull. Read the stage-2 docs. Check health with `status.sh`. Check disk, clean up (§7) and post free disk on the bus. Central: a 3-round mixed regression run equals sim (the system still works after stage 1). | `S2 S0-ready` (central, after all three framework agents posted health) |
| **S1 Core** | central | §4.1–§4.3, conformance v2, `sysmon.py`, regression to stage 1, selfcheck v2 on central | `S2 S1-core` |
| S1 (parallel) | framework agents | Prepare without touching the central deployment:<br>• research their framework's payload limits and knobs (E4) and record the findings;<br>• implement the flhub/0.2 adapter extensions (§4.4) and test them on an island;<br>• prototype the native FedAvg baseline (E7) on their own VM;<br>• write `netem.sh`, `inject.sh` and `straggler.sh` for their VM (§8, E6);<br>• write scripts for the E5 extra sites;<br>• prepare data provisioning;<br>• review the S1 code central pushes | info messages |
| **S2 Rollout** | framework agents, central deploys | Roll out `flhub_core` 0.2 (§4.5), the flhub/0.2 adapters, and the server-side payload knobs from the S1 research. Provision data variants. Selfcheck v2 on hospitals and runtimes. Conformance v2 against central. Ledger rows. Commit the E7 native baseline code plus a central run script (`deploy/central/<fw>/native/`). | `S2 S2-<fw>`; then central: `S2 S2-mixed` (3-round mixed run, new dataset, `dir0.3`, `fedadam`, equals sim) |
| **S3 Tuning** | central (sim only) | Tuning grid on the validation split (`EXPERIMENTS.md` §S3). Freeze `configs/stage2/` at a SHA. Every agent confirms:<br>• `git diff <its rollout SHA> <frozen SHA> -- flhub_core clients/<fw> adapters/<fw>` is empty;<br>• its hospitals report the content-hash `code_version` of `flhub_core` at the frozen SHA. | `S2 S3-frozen <sha>` |
| **S4 Campaign** | central runs every final run; framework agents arm E6 injections on their VM and analyse their E7 runs | Experiments E1–E7 in the order of `EXPERIMENTS.md` §Order, keeping `docs/report/stage2/runs.md` updated | `S2 S4-E<n>` per experiment; `S2 S4-done` |
| **S5 Report** | all | Figures and `REPORT_STAGE2.md` (central). Per-agent sections `docs/report/stage2/<role>.md`. Ledger final. End-state cleanup and evidence. W&B check. DoD. | `S2 report-ready` per agent, then central broadcasts `S2 all-done` |

**After `S3-frozen`**, nobody changes code, images, FABs or jobs that a final run executes. A necessary fix restarts S3-frozen at a new SHA, and any final run made before the fix is redone or explicitly shown unaffected.

One exception: during E4a, **server-side size or streaming knobs** may be committed (ADR). They do not change numerics. Each run records its own SHA, and later runs use the newest one.

## 6. Run discipline

- **Central runs every final run, one at a time**, on central, including the E7 native runs from the framework agents' committed scripts. Framework agents have no shell on central.
- **Disturbing the system:** a framework agent who needs to disturb the system for its own non-final work (for example testing `netem.sh` against central, or an E7 dry run) asks first:
  - `request` "RUNLOCK acquire <what> <est-min>" to central;
  - central replies "RUNLOCK granted <what>" when no final run is active;
  - the agent sends `info` "RUNLOCK release <what>" when done.

  Central does not start final runs while such a lock is held.
- **E6 injections run inside central's final runs.**
  1. Central first sends `request` "ARM <run> <site> <action> at round <N> +<s>s".
  2. The framework agent arms its `inject.sh`, which watches the local client log for the start of round N and then acts. It replies "ARMED".
  3. Only then does central start the run.
- **CPU hygiene on central:** no sim or tuning jobs on central during E3/E4/E5/E7 runs. During E1, E2 and E6 runs, sim jobs may run with `nice -n 19` and at most 2 workers.
- **Final run procedure:**
  1. Commit the config (frozen SHA). Broadcast "RUN start".
  2. Run. No live W&B logging (§4.3).
  3. Broadcast "RUN end". Run `scripts/stage2/finalize_run.py <run_dir>`, which:
     - applies the gate for the run's mode (table below);
     - on PASS: creates the W&B run in `AgnosticFL-stage2` from `metrics.jsonl` (tag `final`, group `E<n>`, name from `EXPERIMENTS.md`, summary incl. `inv1_*`), appends a row to `docs/report/stage2/runs.md`, and only then applies retention (§7);
     - on FAIL: uploads nothing and keeps all files. Investigate, fix, and redo the run.

  | mode | gate before upload |
  |---|---|
  | `real` | INV-1 passed (full sim or replay) |
  | `sim` (extra seeds) | the real seed-2026 run of the same config passed INV-1 (`inv1_method=sim-counterpart:<run>`) |
  | `reference` (centralized, local-only) | finished, all metrics finite (`inv1_method=reference`) |
  | `native` (E7) | finished; `native_vs_hub_max_diff` per round computed against the matching hub run and recorded |
- **Statistics:** for E1/E2, seeds 2027 and 2028 run in `hub.sim` only. This is allowed **because** the real seed-2026 run of the same config passed INV-1; the report must say so explicitly. Seeds change model init and data order only. Partitions stay fixed (`partition_seed` 2026).
- **Timing statistics:** round 1 is warm-up. Report the median and IQR over the remaining rounds, and record whether netem was active.
- **Result tables** in the repo are Markdown or JSON (`*.csv` is gitignored). Figures are PNG, ≤ 300 KB each, in `docs/report/stage2/figures/`.

## 7. Budgets and housekeeping

- **Wall clock:** aim to finish S4 within ~48 h of `S2-mixed`. The runner's safety cap is 96 h. Each final run should take ≤ 60 min for CNN models and ≤ 3 h for `resnet18-gn`. If a run would exceed that, reduce rounds or the subset for that model and record an ADR; keep runs inside one experiment comparable.
- **Disk:** each VM has ~18 GB; keep ≥ 3 GB free at all times. Check `df -h /` before every large step.
  - **Retention** is applied only **after** `finalize_run.py` has passed. Until then everything is kept.
    - Then keep the global weights of round 0, every 10th round and the last round.
    - For replay-checked runs, keep rounds 0–3, last−1 and last, with their aggregator states.
    - Hashes of every round stay in `metrics.jsonl`.
  - Hub-core blob GC after every finalized run.
  - **Payload runs (`payload-*`):**
    - Delete the previous round's blobs during the run; the echo hashes in `metrics.jsonl` suffice for INV-1.
    - Purge framework stores between runs: NVFlare job workspaces, old vantage6 tasks and results in the server DB, and Flower SuperLink DB growth. The framework agents provide `deploy/central/<fw>/purge.sh`.
    - Before each size, require free disk ≥ 3 GB + 4 × payload × (sites in scope) on central. Otherwise stop the sweep and record a **testbed disk limit**, not a framework limit.
  - `docker image prune`: keep only the current and previous `v6-train` tags.
  - `uv cache prune`.
  - Below 3 GB free after cleanup: `blocker` to `human`.
- **RAM** (31 GB per VM): stop the payload sweep for a framework when any process exceeds 70 % of RAM, and record it as that framework's limit.
- **Agent cost:** while waiting, use `tools/bus wait --timeout 600` in a loop. Do not invent busywork. Useful parallel work is listed in your role file.

## 8. Fault injection and network shaping: safety rules

1. Only shape or interfere with framework traffic:
   - destination or source ports 8002 (NVFlare), 7601 (vantage6), 5050 (registry) and 9092 (Flower), plus the E5 equivalents;
   - between central and the framework VMs only.

   **Never** touch port 22 (SSH), 7700 (agent bus), DNS, or the default route as a whole. Never change cloud firewall or security groups. Never reboot a VM.
2. **Before** applying any `tc` rule, schedule its automatic removal. The deadline is the planned duration + 15 min, at most 3 h. Use a systemd timer, which survives an agent crash:

   ```bash
   sudo systemd-run --on-active=<minutes>m --unit "flhub-netem-revert-$(date +%s)" /usr/sbin/tc qdisc del dev <iface> root
   ```

   After the experiment, remove the rule yourself and verify with `tc qdisc show dev <iface>`. Find the interface with `ip route get 145.38.205.58` (usually `ens3`).
3. Use scripts, not ad-hoc commands:
   - framework VMs: `deploy/<fw>-vm/netem.sh {apply <profile>|clear|show}`;
   - central: `deploy/central/netem.sh {apply <profile> <peer-ip>...|clear|show}`. It takes **all peers in one call** and builds one root qdisc (for example `prio`/`htb` with one class and `netem` leaf per peer, filtered by peer IP and framework port), so one revert timer covers it.

   The scripts must be idempotent and must refuse to run without the revert timer. Shape egress on **both** ends (central → hospital for downloads, hospital → central for uploads); ingress shaping via `ifb` is optional.

   Port-based shaping cannot single out one of two sites on the same VM, so E6 stragglers use CPU throttling (`EXPERIMENTS.md` E6b), not netem.
4. If `netem` is missing ("Specified qdisc kind is unknown"), run `sudo apt-get install -y linux-modules-extra-$(uname -r)`. If that is unavailable, use `tbf` for bandwidth only and record an ADR.
5. **Process faults:**
   - Kill or restart only processes on your own VM. Central may kill hub, adapter and framework-server processes on central.
   - Announce on the bus before injecting: `info` "E6 inject <what> at <UTC>".
   - Watchdogs must bring everything back. Before the next measured run, every `status.sh` must be HEALTHY.
6. **Owner escape hatch:** `sudo tc qdisc del dev ens3 root` on any VM removes all shaping.
7. **Auto mode.** Stage-2 agents run in Claude Code auto mode: a classifier reviews each action, and `~/.flhub/stage2/claude-settings.json` adds hard deny rules (force push, `--no-verify`, reboot, firewall, stopping SSH or the bus).
   - If the classifier blocks a step this plan needs (for example a `sudo tc` command), do not work around it.
   - Report it to `human` as a `blocker` with the exact command and continue with other work.
   - The owner either runs the step or adjusts the settings.
   - An experiment that stays blocked is reported as "not run (blocked)", with the reason. It does not fail the DoD as long as the evidence is in the report.

## 9. Definition of Done (stage 2; all must be true and verified)

1. Milestones S0-ready, S1-core, S2-<fw> ×3, S2-mixed, S3-frozen and S4-done were broadcast. Every MUST and SHOULD experiment in `EXPERIMENTS.md` has its final runs. A measured limit or failure counts as a result when the evidence is documented, for example a payload size a framework cannot carry. So does a step blocked by auto mode that the owner did not unblock (§8.7).
2. INV-1 holds for every final real run; `docs/report/stage2/runs.md` lists the method and max difference per run. INV-4 is checked by re-running one E1 config and one E2 config from the frozen SHA.
3. Every adapter passes conformance v2 against the central deployment. At the end, every `status.sh` is HEALTHY for the six base sites; stopped E5 extra sites are documented.
4. The ledger is complete. `git diff --stat stage1-final..HEAD -- clients/` is reported and every change is justified.
5. W&B `AgnosticFL-stage2` contains only finished final runs named as in `EXPERIMENTS.md`, tagged `final` and grouped `E1`–`E7`. Project `AgnosticFL` still contains exactly its six stage-1 runs.
6. These are pushed: `REPORT_STAGE2.md` (following `docs/agents/stage2/REPORT_TEMPLATE.md`), its figures, the per-agent sections, `docs/report/stage2/bus_transcript.md` (stage-2 part) and the ledger. `README.md` and `REPORT.md` each have one link to the stage-2 report.
7. `main` is green: `pytest` passes (unit tests, conformance against mock, aggregator tests). Every commit is authored and committed by Zare2001 with no AI attribution.
8. Clean end state on every VM:
   - no `tc` rules (`tc qdisc show` shows defaults);
   - no leftover revert timers or experiment processes;
   - E5 extra sites stopped;
   - ≥ 3 GB free disk;
   - base hospitals still connected.

   Framework agents clean up **at "S2 final-call"** and put the evidence (command outputs) in their `report-ready` message. Central does the same for central.
9. Central broadcast `S2 all-done` only after receiving `report-ready` with end-state evidence from all three framework agents. Then each agent creates its own `~/.flhub/stage2/DONE`. Each agent verifies the DoD items it can see; central verifies the rest from the evidence the others sent.

## 10. Fallbacks (use when blocked > 45 min; record an ADR)

- **Bit-exactness breaks for a model:**
  1. Check threads = 1, `use_deterministic_algorithms`, and identical kernels (selfcheck v2 per model on each runtime).
  2. If it still differs, use a tolerance with evidence: relative ≤ 1e-5 on weights and ≤ 0.1 pp on final accuracy.
- **A framework cannot carry a payload size:**
  - Record the limit, the error, the component, and the knob that would lift it (server- or hospital-side).
  - Server-side knobs may be changed (ADR).
  - Never change hospital code to beat a limit. A limit is a result.
- **Extra E5 sites for a framework not up within 2 h:** run E5 with the frameworks that work and document the gap.
- **netem unavailable even after installing modules:** use `tbf`. If that also fails, mark E4b "not measured" with the evidence. Do not simulate bandwidth inside the adapters.
- **Native baseline (E7) not feasible for a framework:** document why and what was tried. The other frameworks still count.
- **Disk or RAM pressure:** apply §7. If it persists, reduce the sweep and record the reason.

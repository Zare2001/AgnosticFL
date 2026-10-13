# Stage 2 — Experiment matrix

Priority:

- **MUST**: E1, E2, E4, E6.
- **SHOULD**: E3, E5, E7.
- Both are required for the stage-2 Definition of Done (PLAN.md §9).
- Items marked *Could* are optional. Do them only when the whole DoD is otherwise met and ≥ 12 h of the 96 h cap remain.

Central runs, finalizes and uploads **every** final run (PLAN.md §6), including E7's native runs from the framework agents' committed scripts.

## Common settings (unless a section overrides them)

- **Dataset:**
  - `D` = the multi-class MedMNIST dataset chosen in the stage-2 dataset ADR, at 28 px.
  - Site partitions come from the official training split. Tuning uses the official validation split. Final evaluation uses the official test split.
- **Sites:** the 6 base sites in this order: `nvf-h1 nvf-h2 v6-h3 v6-h4 flw-h5 flw-h6`.
- **Model and training:**
  - model `cnn-small`;
  - 1 local epoch, batch 64, client SGD (lr from S3), momentum 0;
  - training seed 2026; `partition_seed` 2026 (fixed for every seed).
- **Rounds:** 50 for E1/E2 (non-IID needs more than the 30 of stage 1); per section otherwise.
- **Aggregator:** `fedavg`. Participation: all sites. `round_retries` 1.
- **Learning metrics:**
  - test loss, accuracy, balanced accuracy, macro AUC;
  - site-shaped accuracy (min / mean / std);
  - rounds-to-target (0.95 × centralized accuracy);
  - wall time.
- **System metrics:** round time and its breakdown (PLAN.md §4.3); sysmon summaries from all four VMs.

### Partitions (`pid`)

| pid | definition |
|---|---|
| `iid` | stratified random split, equal sizes |
| `dir1.0`, `dir0.3`, `dir0.1` | **label skew with equal site sizes** (Hsu et al. 2019): every site k draws class proportions q_k ~ Dirichlet(α·p), where p is the global class prior, and receives N/6 samples drawn per class according to q_k, without replacement. When a class pool runs out, the rest is redistributed deterministically over the remaining classes in proportion to q_k. |
| `qty` | IID labels, **quantity skew**: site sizes ∝ `nvf-h1 1, nvf-h2 32, v6-h3 2, v6-h4 16, flw-h5 4, flw-h6 8` (sum 63), so every framework has a small and a large hospital |
| `fwcorr` | **framework-correlated skew**, like regions or consortia: for every class, proportions over the 3 frameworks ~ Dirichlet(0.3·1₃); inside a framework, split IID over its 2 sites. Site sizes vary as a consequence; report them. |
| `mix` | `dir0.3` class proportions combined with the `qty` sizes (label + quantity skew) |
| `iid12` | `iid` over the 12 sites of E5 |

The partition manifest (sizes, label histograms, sha256) is committed. Its figure goes into the report: a site × class heat map per pid.

### Run naming and W&B fields

- Name pattern: `s2-<exp>-<variant>[-seed<NNNN>]`, exactly as listed below.
- Group: `E<n>`.
- Tags: `final`, `E<n>`, `part:<pid>`, `agg:<name>`, `model:<id>`, `mode:<real|sim|reference|native>`.
- Config must include: git SHA, `flhub_core.code_version()`, framework versions, partition manifest sha256, aggregator params, participation policy, netem profile.
- Summary must include: final metrics, `inv1_method`, `inv1_max_diff` (or `native_vs_hub_max_diff`).

---

## S3 — Tuning grid (sim only; validation split; not uploaded)

Tune on `dir0.3`, 30 rounds, seed 2026. Pick by final **validation** accuracy, then apply the chosen values to every pid of that aggregator.

| What | Grid |
|---|---|
| client lr (with `fedavg`; reused by all aggregators) | 0.01, 0.03, 0.05, 0.1 |
| `fedavgm` η (β 0.9; effective step η/(1−β)) | 0.1, 0.3, 1.0 |
| `fedadam` η | 1e-3, 3e-3, 1e-2, 3e-2 |
| `fedyogi` η | 1e-3, 3e-3, 1e-2, 3e-2 |
| `fedadagrad` η | 1e-2, 3e-2, 1e-1 |
| `fedprox` μ | 0.001, 0.01, 0.1 |

That is 21 sim runs, about 2 h with `--workers 4`. Commit the full result table to `docs/report/stage2/tuning.md`.

**A\*** is the best of {fedavgm, fedadam, fedyogi, fedadagrad, fedprox} by final validation accuracy in this grid. Record it in `tuning.md`; E3 and E6 use it. Freeze the chosen values in `configs/stage2/` (S3-frozen).

---

## E1 — Data heterogeneity (MUST)

Question: how much does each kind of skew hurt plain FedAvg, and does framework-correlated skew behave differently from site-level skew?

| Runs | Mode | Count |
|---|---|---|
| `s2-e1-<pid>-fedavg` for pid ∈ {iid, dir1.0, dir0.3, dir0.1, qty, fwcorr, mix} | real, 6 sites | 7 |
| `s2-e1-<pid>-fedavg-seed2027`, `-seed2028` (same pids) | sim | 14 |
| `s2-e1-centralized` (pooled training split, 50 epochs) | reference | 1 |
| `s2-e1-local-<pid>` for pid ∈ {iid, dir0.1}: each site trains alone for 50 epochs; one run logs all 6 sites | reference | 2 |

Pass: INV-1 (full sim) for each real run.

Figures:

- test accuracy vs round, one line per pid;
- final accuracy with seed spread;
- worst-site accuracy (site-shaped) per pid.

## E2 — Aggregation algorithms (MUST)

Question: which server or client optimizer copes best with heterogeneity, and is adding one a hub-only change?

| Runs | Mode | Count |
|---|---|---|
| `s2-e2-<pid>-<agg>` for agg ∈ {fedavgm, fedadam, fedyogi, fedadagrad, fedprox}, pid ∈ {iid, dir0.3, dir0.1} | real | 15 |
| the same 15 configs, `-seed2027` and `-seed2028` | sim | 30 |

The `fedavg` cells of the comparison are the E1 runs `s2-e1-{iid,dir0.3,dir0.1}-fedavg` and their seeds. Do not duplicate them.

Pass: INV-1 (full sim) for each real run. Also record the hospital-side diff per aggregator; the expected value is 0 lines, and for FedProx 0 lines in `clients/` because μ lives in `flhub_core`.

Figures:

- final accuracy, aggregator × pid, mean ± std over 3 seeds;
- accuracy vs round for `dir0.1`;
- rounds-to-target table.

## E3 — Model scale (SHOULD)

Question: how do accuracy, round time and its breakdown change with model size?

| Runs | Mode | Count |
|---|---|---|
| `s2-e3-<model>-<agg>` for model ∈ {cnn-medium, resnet18-gn}, agg ∈ {fedavg, A*}, pid `dir0.3`. Rounds: 50 for `cnn-medium`, 20 for `resnet18-gn` (+ ADR if fewer are needed for the budget). | real | 4 |
| *Could:* `s2-e3-cnn-medium64-fedavg` (64 px MedMNIST+) | real | 1 |

The `cnn-small` cells are `s2-e1-dir0.3-fedavg` and `s2-e2-dir0.3-<A*>`.

INV-1:

- full sim for the CNNs;
- replay spot-check (rounds 1, 2, 3 and last) for `resnet18-gn`.

No sim jobs on central during these runs.

Report parameters, payload MB and the per-round breakdown: train / overhead / aggregation / eval, per framework. Also report CPU and RSS per VM from sysmon.

## E4 — Payload and bandwidth (MUST)

### E4a — payload sweep

- Model `payload-<S>` (echo training), 3 rounds; round 1 is warm-up and the median of rounds 2–3 is reported.
- Sizes S ∈ {1, 8, 32, 64, 128, 256, 512} MB.
- Scopes: {nvflare, vantage6, flower, mixed}. Single-framework scopes use only that framework's 2 sites.
- Runs: `s2-e4a-<scope>-<S>mb`, up to 28.
- Before E4a, the hub-side caps are ≥ 1024 MB and the S1 server-side knobs are deployed (PLAN.md §4.4). Disk rules and store purges apply (PLAN.md §7).
- **Stop rule per scope:**
  1. On the first failure, apply a **server-side** knob if one exists (committed, ADR) and retry that size once.
  2. If the retry passes, continue with the next size.
  3. Stop at the second failure, or when the disk/RAM rules stop the sweep (recorded as a testbed limit).
- *Could:* 1024 MB if a scope passes 512 MB and RAM and disk allow.

Record per run:

- round time and per-site time;
- bytes on wire per framework and round, and the overhead ratio (bytes ÷ payload);
- peak RSS of the framework server, the adapter and each client;
- success, or the failure mode: error text, component, limit, knob, and whether the knob is server- or hospital-side.

Pass: INV-1 for every successful run (the echo is deterministic; hashes suffice).

Figures:

- round time vs payload size, log–log, one line per framework, with failure markers;
- bytes overhead ratio;
- peak RSS vs payload.

### E4b — bandwidth sensitivity

- Model `payload-45` (≈ `resnet18-gn` size), mixed scope, 3 rounds.
- netem profiles applied to every hospital link in both directions (PLAN.md §8; central shapes all three peers in one call):

| profile | rate | delay |
|---|---|---|
| `none` | — | — |
| `wan1g` | 1 Gbit/s | 5 ms |
| `wan100` | 100 Mbit/s | 20 ms |
| `wan20` | 20 Mbit/s | 40 ms |

- Runs: `s2-e4b-<profile>` (4).
- **Lower bound per round:** each VM link carries 2 sites, the download (2 × P) precedes training, and the upload (2 × P) follows it. So lower bound = `4 × P / rate`, × 4/3 for vantage6 if its transport is base64.
- Report measured round time against that bound per framework. Efficiency = lower bound ÷ measured.

## E5 — Number of sites (SHOULD)

Question: how does round time grow with the number of sites per framework, and what does that cost the central VM?

| Runs | Mode | Count |
|---|---|---|
| `s2-e5-6sites`: `iid` over the 6 base sites, 20 rounds | real | 1 |
| `s2-e5-12sites`: `iid12` over 12 sites (4 per framework: base + `nvf-h7 nvf-h8 v6-h9 v6-h10 flw-h11 flw-h12`), 20 rounds | real | 1 |
| *Could:* `s2-e5-uneven`: nvflare 2, vantage6 4, flower 4 sites (uneven framework share), 20 rounds | real | 1 |

Pass: INV-1 (full sim).

Report:

- round time and per-framework fan-out time (dispatch → last result);
- central CPU and RSS;
- adapter-side timings.

## E6 — Robustness (MUST)

Common settings: `dir0.3`, `cnn-small`, 20 rounds, unless noted.

Injections are **armed before central starts the run** (PLAN.md §6: ARM → ARMED). Every framework agent provides, in S1, for its own VM:

- `deploy/<fw>-vm/inject.sh arm <site> <action> --round N --after S` watches the local client log for the start of round N, waits S seconds, then acts. Actions: `kill` (SIGKILL the client or training process) and `restart`. `inject.sh disarm` cancels.
- `deploy/<fw>-vm/straggler.sh {on <site> <cpu%>|off <site>}` throttles **one** site's training CPU. Examples:
  - restart that client inside `systemd-run --scope -p CPUQuota=<cpu%>`;
  - for vantage6, a cgroup or `docker update --cpus` on the containers of that node.

  Mechanism per framework by ADR. netem cannot single out one of two sites on a VM.

Central injects the hub, adapter and framework-server faults on central.

| ID | Scenario | Runs (real) |
|---|---|---|
| E6a | **Dropout.** In rounds 5, 10 and 15, kill one hospital client mid-training: an NVFlare site, then a vantage6 site, then a Flower site. Two policies:<br>• strict: all sites required; the round fails, is retried, and succeeds after the watchdog restart;<br>• `min_sites: 5`: the round completes without that site, via partial collect + per-site cancel. | `s2-e6a-strict`, `s2-e6a-minsites` |
| E6b | **Straggler.** One site per framework in turn runs at 10 % CPU (`straggler.sh`), with `deadline_s` set to ~2× a normal round and `min_sites: 5`. | `s2-e6b-straggler` |
| E6c | **Partial participation.** `fraction: 0.5` (3 of 6 sites per round, seeded), 50 rounds, with fedavg and A*. | `s2-e6c-fedavg`, `s2-e6c-<A*>` |
| E6d | **Hub crash and resume.** The `dir0.3` + A* config of E2 (50 rounds). At round 20, SIGKILL the orchestrator and all three adapters, restart them, and `--resume`. The final weights must equal `s2-e2-dir0.3-<A*>` **bit-exactly** (hash equality), which proves the server-optimizer state survived. | `s2-e6d-resume` |
| E6e | **Framework-server restart.** Restart the NVFlare server, the vantage6 server and the Flower SuperLink, one at a time, each once between rounds and once mid-round. The clients and relay jobs or runs must recover without manual action, and the run must complete (strict policy with retries). | `s2-e6e-server-restart` |

Pass:

- INV-1 against sim with the recorded participants (`--participants-from`) for E6a/b/c/e;
- hash equality with the uninterrupted run for E6d;
- no manual action needed anywhere except the armed injection itself.

Report a table, scenario × framework, with these columns:

- outcome;
- detection time;
- recovery time;
- rounds lost or retried;
- manual action needed (must be "none");
- INV-1 result.

## E7 — Overhead vs native FedAvg (SHOULD)

For each framework `fw`, compare the framework's own idiomatic server-side FedAvg (no hub) with the hub path, using:

- the same 2 hospitals;
- the same `flhub_core.train`;
- the same config.

The native server part runs on **central**, next to the hub, so the comparison is fair. Native FedAvg per framework:

- **NVFlare:** the built-in FedAvg controller or recipe, with aggregation weights = `num_examples`.
- **Flower:** a ServerApp with the `FedAvg` strategy, weighted by the number of examples.
- **vantage6:** the idiomatic central function + partial tasks. Document where the central part runs.

**Who does what:**

- The framework agent writes the native code and `deploy/central/<fw>/native/run.sh <config> <out_dir>`. The script writes `metrics.jsonl` in the hub's format, with per-round global weights hashes and checkpoints.
- The framework agent commits it before S3-frozen and dry-runs it on its island.
- Central runs it under the normal procedure, pausing the hub's relay job or run if needed. Central finalizes (mode `native`) and uploads it.
- The framework agent analyses the results and writes them up.

| Config | Runs per framework |
|---|---|
| C1: `iid` (that framework's 2 sites), `cnn-small`, `fedavg`, 20 rounds | `s2-e7-<fw>-native-cnn`, `s2-e7-<fw>-hub-cnn` |
| C2: `payload-45` echo, 5 rounds | `s2-e7-<fw>-native-payload`, `s2-e7-<fw>-hub-payload` |

Report:

- per-round max abs weight difference native vs hub. Expected ≤ 1e-5; if a native aggregator differs in precision or order, quantify and explain it.
- median round time native vs hub, and the overhead in seconds and %;
- extra processes and memory on the hub path.

---

## Order (S4)

S4 starts after S3-frozen.

1. **E4a**: system risk first.
2. **E6d dry run**: one 6-round resume test, not a final run, to de-risk resume early.
3. **E1 → E2**: real runs one after another; sim seeds fill the gaps (`nice 19`, ≤ 2 workers).
4. **E6** (a, b, c, d, e).
5. **E3**.
6. **E5**: bring the extra sites up, run, then stop them.
7. **E7**, for each framework.
8. **E4b**: netem last, so a shaping mistake cannot spoil earlier runs.

## Run count (final runs uploaded to W&B)

| | real | sim | reference | native | total |
|---|---|---|---|---|---|
| E1 | 7 | 14 | 3 | | 24 |
| E2 | 15 | 30 | | | 45 |
| E3 | 4 | | | | 4 |
| E4a | ≤ 28 | | | | ≤ 28 |
| E4b | 4 | | | | 4 |
| E5 | 2 | | | | 2 |
| E6 | 7 | | | | 7 |
| E7 | 6 | | | 6 | 12 |
| **total** | **≤ 73** | **44** | **3** | **6** | **≤ 126** |

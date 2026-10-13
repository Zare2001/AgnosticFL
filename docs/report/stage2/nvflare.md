# Stage 2 — NVFlare agent (145.38.189.142)

Status: draft during S2–S4; experiment results are filled in from the final runs (`runs.md`) in S5.

## 1. Scope

- Hospitals `nvf-h1`, `nvf-h2` (NVFlare 2.9.0 clients) and the E5 extra sites `nvf-h7`, `nvf-h8` on the nvflare VM.
- NVFlare adapter on flhub/0.2 (`adapters/nvflare/`), its relay job and the central-side scripts (`deploy/central/nvflare/`).
- Rollout of `flhub_core` 0.2 the NVFlare way, payload limits (E4), fault-injection tools on the VM (E6), the native FedAvg baseline (E7).

## 2. What changed

| area | change | ADR |
|---|---|---|
| relay | one NVFlare task per site (`Controller.send`, no broadcast deep copy), per-site results/errors to the adapter as they arrive, `/relay/job` polling cancels sites the hub no longer waits for | ADR-011 |
| adapter | offline sites fail alone at dispatch; a site offline for 30 s during a round fails; relay job `min_clients` 1 with submit gating (all base sites or 30 s); E5 extras registered but never blocking; stale relay jobs after a server restart are replaced | ADR-011 |
| job | relay job ships `custom/client.py` + `custom/flhub_core/` (the `.py` files of the deployed checkout) | ADR-018 |
| hospitals | `flhub_core` removed from the client venv once (switch), per-site watchdog, data variants next to the stage-1 file | ADR-018, ledger |
| E6 | `inject.sh` (log-triggered kill / kill-job / restart / straggler on-off), `straggler.sh` (CPU-quota scope, no restart), `restart_server.sh` | ADR-012 |
| E7 | `adapters/nvflare/native.py` (NativeFedAvg = built-in FedAvg + recording hooks), `native/run.sh` | ADR-019 |
| E4/E5 | `purge.sh` (FLARE API job deletion), `provision_extra.sh` (`--add-client`, same root CA), `extra.sh`, `netem.sh` | — |

`clients/nvflare/client.py` is unchanged since `stage1-final` (0 lines). Ledger rows: `docs/report/stage2/ledger.md` (nvflare).

## 3. Rollout of flhub_core 0.2

Channel: the relay job's custom code (BYOC). NVFlare 2.9.0 **appends** a job's `custom/` directory to the job process's `sys.path` (`utils/job_launcher_utils.py:486-503`), so a `flhub_core` installed in the hospital's environment shadows the job's copy. This was observed on the real system: after central deployed the 0.2 job, both hospitals still ran `0.1.0+9c6b064c1e96`.

Procedure (all scripted, rehearsed on an island first):

| step | who | what | wall time | client restarts | downtime |
|---|---|---|---|---|---|
| 0 | hospital (stage-2 prep) | pin the stage-1 `flhub_core` as a copy (the stage-1 venv had an editable link to the VM's git checkout) | 1 s | 0 | 0 |
| 1 | hospital | provision the data variants next to `<site>.csv` (manifest-checked) | 12.9 s | 0 | 0 |
| 2 | central | `install.sh` (relay job with `custom/flhub_core`) + `stop.sh && start.sh` | — | 0 | relay job restart |
| 3 | hospital | `switch_s2.sh`: remove `flhub_core` from the venv, restart the client | 82.6 s | 1 per site | 50.3 s per site |

After the switch, selfcheck v2 in the hospital runtime (venv Python + job `custom/flhub_core`) gives `sha256:ac074a0b…` at both sites (= central), and every later `flhub_core` version reaches the hospitals with the next relay job, with no hospital step. Lines changed in `clients/nvflare/`: 0.

What a real hospital would do: once, remove the project library from its NVFlare client environment and restart the client; afterwards only accept jobs.

## 4. Limits and knobs (E4)

How a model travels: FLModel numpy params are serialized with FOBS; `NumpyArrayDecomposer` is a `ViaDownloaderDecomposer`, so arrays go out-of-band through the sender's DownloadService in 2 MiB chunks; the 2 GiB gRPC frame limit applies to inline messages only. Knob table (file:line, side, default): worklog entry "NVFlare 2.9.0 payload limits and knobs".

Island (relay path, localhost, defaults): 128 MB 5–6 s/round, 256 MB 9–10 s, 512 MB 16–17 s (VM peak memory +5.3 GB), 1024 MB 32.8 s. No server-side knob was needed before E4a.

E4a/E4b on the real system: (S4 results).

## 5. Experiments supported

- E6 tools and their island behaviour: `docs/report/stage2/evidence/nvflare/e6-tools-island.md`. NVFlare semantics that shape E6: a crashed training process makes the server fail the **whole** relay job (`fed_server.py:944-951`, no knob); a killed client is only failed by our adapter (30 s offline) and re-enters a job after a relay-job restart (~60 s); a cancelled task cannot be stopped at the client.
- E7 native baseline design and island dry runs: `docs/report/stage2/evidence/nvflare/e7-native-island.md`.
- Final-run results (E4a, E4b, E5, E6, E7): (S4 results).

## 6. Problems and fixes

| problem | symptom | root cause | fix | SHA | time lost |
|---|---|---|---|---|---|
| hospital code followed the VM's git checkout | `__editable__.flhub_core` in the hospital venv | stage-1 `install.sh` used `-e flhub_core` | pinned copy, later removed (ADR-018) | b72c4e8, 82e9f07 | 10 min |
| `stop.sh <site>` disabled the watchdog of every site | latent (would have hit E5) | one global cron tag | per-site `.enabled` markers, one cron line per sites dir | a996bab | 20 min |
| job-shipped 0.2 not used at the hospitals | clients ran 0.1 with a 0.2 job | `custom/` appended to `sys.path` | remove the venv copy + client restart | 72c657d, 82e9f07 | — |
| job export dropped `client.py` | relay job without `custom/client.py` | relative script path resolved at export time, export ran outside its directory | export inside the script dir + post-export check | 72c657d | 5 min |
| native job rejected | `ComponentNotAuthorized` | server app without custom code is held to the kit's class allow-list | ship the workflow as BYOC server code | 271bf12 | 10 min |
| FedAvg dropped `train_config` after round 1 | (would train defaults from round 2) | `FLModelUtils.update_model` replaces the meta | re-attach in `send_model()` | 271bf12 | — |
| sites offline > 5 min after two server restarts | keeper looped on a failing abort | job store kept the old relay job RUNNING; abort answered "not running" | treat as stale (delete or ignore) and resubmit; end a silent relay's job at once | 007bf3a | 20 min |

## 7. Recommendations

- NVFlare jobs that carry library code: keep that library out of the client environment (custom code is appended to `sys.path`), or run the script as an external process (script directory first).
- Use one task per site (`Controller.send`) for per-site results and cancels; it also avoids the broadcast deep copy of large models.
- Plan for job-level failure: one site's job-process crash ends the job for every site; keepers must resubmit, and must handle job-store entries that outlive a server restart.
- NVFlare's built-in FedAvg aggregates in float32 in arrival order: not bit-equal to a float64 reference unless the weights are exactly representable; the difference grows through later local training.

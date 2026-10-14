# NVFlare E6 tools — island test (nvflare VM, 2026-10-13)

Island: NVFlare 2.9.0 server `localhost` + adapter (flhub/0.2, ef722b7 code) + hub core on the nvflare VM;
island clients nvf-h1/nvf-h2 (own kits, hospital venv, stage-1 data). Rounds driven through the adapter with
one fixed model (`~/flhub-island/drive.py`, not part of the repo). Bus announcements off (`NVF_INJECT_BUS=true`).

| test | injection | round outcome | NVFlare behaviour | client-side recovery |
|---|---|---|---|---|
| kill, strict, retries 1 | `inject.sh arm nvf-h1 kill --round 3 --after 0.1` (fired 0.2 s after the round-3 task arrived; client + job process SIGKILLed) | r3: nvf-h2 done 2.0 s; nvf-h1 failed 35.1 s (adapter: offline 30 s); retry waited 34.1 s for online, completed 3.0 s | server failed the relay job 57 s after the kill ("terminal outcome unavailable from nvf-h1: missing job on client"); keeper submitted a new one | client up 4.0 s, registered 6.0 s, job process back 61.8 s |
| kill-job, min_sites 1 | `inject.sh arm nvf-h2 kill-job --round 17 --after 0.1` (training process only) | r17: nvf-h1 done 2.5 s; nvf-h2 failed 4.5 s (task CANCELLED); r18 completed after 15.6 s (waited for the new job) | "Failing job … due to reported failure from nvf-h2: exception" 0.1 s after the kill (`fed_server.py:944-951`, no knob): the whole relay job ends for both sites | new relay job after ~14 s |
| kill-job (2nd run) | `inject.sh arm nvf-h2 kill-job --round 25 --after 0.1` | r25: BOTH sites failed at 4.5 s (nvf-h1 was still training when the job ended); r26 completed after 16.6 s | whole relay job failed at once | job process back after 15.2 s |
| restart | `inject.sh arm nvf-h1 restart --round 21 --after 0.1` (touch restart.fl) | r21-r23 completed (the kit daemon acts after its 5 s poll + 5 s grace, after the short round) | client restarted by the kit daemon | client up 11.0 s, registered 13.0 s, job process back 49.5 s |
| straggler 10 % | `straggler.sh on nvf-h1 10` (scope flhub-straggler-nvf-h1.scope, CPUQuotaPerSecUSec=100ms, client + job pids adopted, no restart) | 5-epoch rounds: nvf-h1 6.5 s and 7.5 s vs 1.5-3.5 s before; `off` -> 2.0 s | site stayed in its relay job; probes kept answering (no false offline) | — |

# fl-hub agent kit — setup guide (for you, the project owner)

This kit makes four Claude Code agents (one per VM) build a framework-agnostic federated learning network together: hospitals on NVFlare, vantage6 and Flower all train one model with plain FedAvg through a central hub, with adapters on the server side and standard clients on the hospital side. The agents coordinate over a small message bus (port **7700** on the central VM), collaborate on GitHub as **Zare2001**, log final runs to W&B, and finish with `REPORT.md`.

## What runs where

| VM | IP | Agent role | Ends up running |
|---|---|---|---|
| central | 145.38.205.58 | `central` (lead) | agent bus, hub core + orchestrator, 3 adapters, NVFlare server, vantage6 server, Docker registry, Flower SuperLink |
| NVFlare | 145.38.189.142 | `nvflare` | NVFlare clients `nvf-h1`, `nvf-h2` |
| vantage6 | 145.38.186.63 | `vantage6` | vantage6 nodes `v6-h3`, `v6-h4` |
| Flower | 145.38.187.20 | `flower` | Flower SuperNodes `flw-h5`, `flw-h6` |

The full plan the agents follow is in `repo/docs/agents/PROJECT_PLAN.md`.

## Before you start

- 4 Ubuntu 22.04/24.04 VMs with a normal sudo user (not root) and outbound internet.
- Claude Code access on each VM (claude.ai login or an `ANTHROPIC_API_KEY`). Four Opus 5.5 agents at max effort running for many hours is expensive; the runner has a safety cap of 96 hours (`FLHUB_MAX_HOURS`).

## Step 1 — GitHub

1. The agents work in your existing repo **`Zare2001/AgnosticFL`** (https://github.com/Zare2001/AgnosticFL). It may be empty or already contain files. The central bootstrap adds the kit in one commit on the default branch and never overwrites existing files (an existing `.gitignore` is extended, not replaced).
2. Make sure your fine-grained token has access to that repo with **Contents: Read and write** and **Metadata: Read**.

## Step 2 — Firewall (central VM)

Allow inbound TCP from the three framework VM IPs (145.38.189.142, 145.38.186.63, 145.38.187.20) to these ports on 145.38.205.58:

| Port | For |
|---|---|
| 7700 | agent bus (all three) |
| 8002 | NVFlare server (NVFlare VM) |
| 7601 | vantage6 server (vantage6 VM) |
| 5050 | Docker registry (vantage6 VM) |
| 9092 | Flower SuperLink (Flower VM) |

On SURF Research Cloud this is done in the workspace's firewall/security settings, not only with `ufw`. Framework VMs need no inbound ports.

## Step 3 — Secrets (on each VM)

Generate one bus token on your laptop and use the same value on all four VMs:

```bash
openssl rand -hex 32
```

Then on each VM:

```bash
mkdir -p ~/.flhub && chmod 700 ~/.flhub
cp fl-hub-agent-kit/secrets.env.example ~/.flhub/secrets.env
nano ~/.flhub/secrets.env        # fill GH_TOKEN, WANDB_API_KEY, AGENTBUS_TOKEN
chmod 600 ~/.flhub/secrets.env
```

Your tokens are deliberately not in any file of this kit: the kit gets committed to GitHub, and the agents read the tokens only from `~/.flhub/secrets.env`.

## Step 4 — Copy the kit and bootstrap (central FIRST)

```bash
scp fl-hub-agent-kit.zip <user>@<vm-ip>:~ && ssh <user>@<vm-ip> 'unzip -o fl-hub-agent-kit.zip'
```

On each VM (after step 3):

```bash
bash fl-hub-agent-kit/scripts/bootstrap_vm.sh central     # on 145.38.205.58 — run this one first
bash fl-hub-agent-kit/scripts/bootstrap_vm.sh nvflare     # on 145.38.189.142
bash fl-hub-agent-kit/scripts/bootstrap_vm.sh vantage6    # on 145.38.186.63
bash fl-hub-agent-kit/scripts/bootstrap_vm.sh flower      # on 145.38.187.20
```

The bootstrap installs git, gh, uv, Claude Code, Docker (central and vantage6), sets git identity to Zare2001 (no-reply email), enables the commit hooks, and on central seeds the repo and starts the agent bus as a systemd service. The other VMs wait for the seed and clone it.

## Step 5 — Log in and start the agents

On each VM:

```bash
exec sudo -iu $USER          # only on central and vantage6, to pick up the docker group
claude                       # log in once (or set ANTHROPIC_API_KEY in secrets.env), then /exit
tmux new -s flhub 'bash ~/AgnosticFL/tools/run_agent.sh'
```

Start central first, then the other three. Detach with `Ctrl-b d`; reattach with `tmux attach -t flhub`.

Each agent runs Opus 5.5 at max effort (`--effort max`) with Haiku 5.5 subagents for lookups, commands, git and docs. It keeps resuming its own session until the project's Definition of Done is met.

## Watching and talking to the agents

```bash
cd ~/AgnosticFL
tools/bus status                         # phase + summary + blockers of every agent
tools/bus log --since 0 --limit 50       # recent messages
tools/bus inbox --as human               # messages addressed to you (e.g. a closed port)
tools/bus send --as human --to all --type info --subject "..." --body "..."
tail -f ~/.flhub/logs/runner.log         # runner restarts
```

You can also follow the GitHub commits and the W&B project `AgnosticFL`.

## Pause, resume, stop

- Pause or stop one agent: `touch ~/.flhub/STOP`, then end the tmux session.
- Resume: `rm ~/.flhub/STOP` and start the tmux command again. It resumes the same session.
- Finished: each agent creates `~/.flhub/DONE`. The result is `REPORT.md` in the repo, plus the W&B project containing only the final runs.

## Good to know

- **Commits are yours only.** Claude's attribution is switched off in `.claude/settings.json`. The git hooks also strip and block any AI attribution, oversized commits and pushes, and forbidden files (secrets, startup kits, data, weights).
- The `attribution` setting is documented but its exact empty-value behaviour isn't, so each agent tests it in phase 0. The hooks are the real guarantee.
- **Rotate both tokens when the project is done.** The GitHub PAT and the W&B key were pasted into a chat.

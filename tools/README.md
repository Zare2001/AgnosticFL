# tools

The reference testbed's small authenticated message and file bus. The machines of the testbed used it to coordinate: the scripts in [`deploy/`](../deploy/README.md) send credentials and monitoring summaries over it, and the stage-2 scripts in [`scripts/`](../scripts/README.md) use it to announce runs, arm fault injections and collect results. It is not needed to run the hub.

| Path | What |
|---|---|
| `agentbus/agentbus.py` | server (`serve`, HTTP on port 7700, SQLite storage) and command-line client: `send`, `inbox`, `wait`, `log`, `status`, `put`, `get`, `export`; standard library only |
| `bus` | wrapper that loads the token and the machine's name from `~/.flhub/` and runs the client |

Every request carries a shared token. Files sent with `put --secret` are encrypted on the client (OpenSSL AES-256-CBC with PBKDF2, the token as passphrase), so the server stores only ciphertext. Outside the testbed, use your own secure channel to hand credentials to hospitals.

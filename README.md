# mcp-hub

Local, config-driven MCP gateway for AI agents. The Hub connects to child MCP servers over stdio, discovers their tool schemas, exposes only configured allowlisted tools, routes calls, and writes audit events to `logs/mcp-hub.jsonl`.

## Run

Python 3.12 is the default project runtime. The launcher checks for `uv` and Python 3.12 before starting.

Run from an interactive terminal the first time:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start.ps1
```

On Linux or macOS:

```sh
bash scripts/start.sh
```

If `uv` or Python 3.12 is missing, the launcher asks before installing. If needed, it installs `uv` under the ignored `.tools/` directory, then uses `uv` to install Python and sync project dependencies. The first install needs internet access and write permission in the repository, plus write access to the current user's uv data directory. Answering No stops without installing anything.

When an MCP host launches the stdio server, it cannot answer an interactive prompt. Run the launcher once from a terminal first if prerequisites are missing. After setup, the launcher runs without prompting. Startup diagnostics go to stderr; stdout remains available to the MCP protocol.

The default runtime config is `config/private/hub.json`. `config/hub.example.json` is the safe template. Each enabled server points to a private per-MCP config under `config/private/mcps/`.

## Jira integration

The initial local config connects to `D:/Visual Studio Code/jira_sync_ticket`. Jira credentials are in the ignored per-MCP file; the Hub passes its path to the Jira MCP using `CENTRAL_CONFIG_PATH`. The Jira repository's existing `.env` is unchanged for standalone use. See `docs/mcps/jira-sync-ticket.md` for each exposed tool and its side effects.

After the first-run setup, run the read-only integration smoke check with PowerShell:

```powershell
$uv = if (Test-Path .tools/uv.exe) { (Resolve-Path .tools/uv.exe).Path } else { "uv" }
& $uv run python scripts/smoke_jira.py
```

On Linux or macOS:

```sh
uv_bin=uv
if [ -x .tools/uv ]; then uv_bin=./.tools/uv; fi
"$uv_bin" run python scripts/smoke_jira.py
```

It verifies Hub tool discovery and calls `jira__check_connection`. It does not sync a ticket or print Jira account details. `jira__sync_ticket` writes files and is not needed to prove connectivity.

## Security

- Never commit `config/private/`, `.env`, or `logs/`.
- Tool arguments and successful results are not written to Hub logs.
- The Hub passes only selected process environment variables to a child, plus values explicitly set in that child's private config.
- Secret values found in private config are redacted from Hub error messages.
- Stdio diagnostics go to stderr or the log file; stdout is reserved for MCP protocol frames.

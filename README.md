# mcp-hub

Local, config-driven MCP gateway for AI agents. The Hub connects to child MCP servers over stdio, discovers their tool schemas, exposes only configured allowlisted tools, routes calls, and writes audit events to `logs/mcp-hub.jsonl`.

## Run

Python 3.10+ and `uv` are required.

```powershell
uv sync
uv run mcp-hub
```

The default runtime config is `config/private/hub.json`. `config/hub.example.json` is the safe template. Each enabled server points to a private per-MCP config under `config/private/mcps/`.

## Jira integration

The initial local config connects to `D:/Visual Studio Code/jira_sync_ticket`. The child server keeps using its existing ignored `.env`; the Hub does not copy credential values. See `docs/mcps/jira-sync-ticket.md` for each exposed tool and its side effects.

Run the read-only integration smoke check with:

```powershell
uv run python scripts/smoke_jira.py
```

It verifies Hub tool discovery and calls `jira__check_connection`. It does not sync a ticket or print Jira account details. `jira__sync_ticket` writes files and is not needed to prove connectivity.

## Security

- Never commit `config/private/`, `.env`, or `logs/`.
- Tool arguments and successful results are not written to Hub logs.
- The Hub passes only selected process environment variables to a child, plus values explicitly set in that child's private config.
- Stdio diagnostics go to stderr or the log file; stdout is reserved for MCP protocol frames.

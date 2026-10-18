# mcp-hub

Local, config-driven MCP gateway for AI agents. The Hub launches child MCP servers over stdio, discovers their tools, exposes only allowlisted ones, routes calls, and writes audit events to `logs/mcp-hub.jsonl`.

**Agents: read [AGENTS.md](AGENTS.md) first.** Its rules are mandatory.

## Run

Python 3.10+ and `uv` are required.

```powershell
uv sync
uv run mcp-hub
```

The Hub reads `config/private/hub.json` (or `MCP_HUB_CONFIG`, which must point inside `config/private/`). Child repositories sit next to this repo and are referenced as `../<repo>`.

## Where to look

| Need | File |
|------|------|
| Rules for agents | [AGENTS.md](AGENTS.md) |
| Add or reconfigure a child MCP | [docs/adding-a-child-mcp.md](docs/adding-a-child-mcp.md) |
| What a child MCP does, its tools and side effects | [docs/mcps/](docs/mcps/) |
| Request flow, failure handling, prompts | [docs/architecture.md](docs/architecture.md) |
| Secrets, audit events, diagnosing failures | [docs/security-and-logging.md](docs/security-and-logging.md) |
| Safe config templates | [config/hub.example.json](config/hub.example.json), [config/mcps/](config/mcps/) |

## Layout

- `src/mcp_hub/config.py`: validates the Hub manifest and per-server private configs, and resolves relative paths.
- `src/mcp_hub/server.py`: starts child clients, exposes allowlisted tools and per-child prompts, routes calls, and writes audit events. It uses the MCP SDK's low-level server so child JSON Schemas and annotations are forwarded unchanged.
- `src/mcp_hub/audit.py`: rotating JSONL audit log with secret redaction.
- `scripts/private_config.py`: inspects or edits a private child config without printing secrets.
- `scripts/check_leaks.py`: blocks machine paths, real emails, and private files from being committed.
- `scripts/smoke.py`: read-only end-to-end check. For each enabled child it checks the Hub prompt and makes one read-only tool call (`CHECKS` table).
- `tests/`: `uv run python -m unittest discover tests`.

## Security summary

- `config/private/`, `.local/`, `.env*`, and `logs/` are gitignored and never committed.
- Tool arguments and successful results are not logged. Known secret values are redacted from error logs.
- A child receives only its `inheritEnv` variables plus its configured `env`.
- stdout is reserved for MCP protocol frames.

# mcp-hub

Local, config-driven MCP gateway for AI agents. The Hub launches child MCP servers over stdio, discovers their tools, exposes only allowlisted ones, routes calls, and writes audit events to `logs/mcp-hub.jsonl`.

**Agents: read [AGENTS.md](AGENTS.md) first.** Its rules are mandatory.

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

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

To connect an AI client, paste this into the agent (replace `<MCP_HUB_PATH>` with this repo's absolute path). Other prompts (instructions only, rule refresh, daily use) are in [docs/connecting-a-client.md §0](docs/connecting-a-client.md#0-for-the-human-the-prompts).

```text
Read <MCP_HUB_PATH>/docs/connecting-a-client.md and follow every step to connect yourself to mcp-hub,
using the file for this client in <MCP_HUB_PATH>/docs/clients/: register one MCP server named mcp-hub,
replace any old entries for its child MCPs, install the always-on rule and the /mcp-hub command.
Ask me before writing global config or removing entries, and report what you changed at the end.
```

Daily use after that: `/mcp-hub <server-id> <task>` (Codex: `$mcp-hub …`), e.g. `/mcp-hub mcp-server-logs show the prod logs for service orders-api`. Without the command installed, type `mcp-hub:<server-id> <task>`.

The Hub reads `config/private/hub.json` (or `MCP_HUB_CONFIG`, which must point inside `config/private/`). Child repositories sit next to this repo and are referenced as `../<repo>`.

## Check connections

```powershell
uv run python scripts/smoke.py
```

It starts the Hub as an MCP host would and prints one line per enabled child MCP:

```text
jira-sync-ticket  PASS  3 tools, jira__check_connection ok
mcp-server-logs   PASS  12 tools, logs__list_openobserve_organizations ok
```

A `FAIL` line names the cause:

| Output | Meaning |
|---|---|
| `not connected: <event> (<error_type>)` | The child didn't start or didn't answer; check `cwd`/`command`. |
| `not connected (no Hub events; see logs)` | No events from this run (rare, e.g. log rotation); rerun. |
| `no exposed tools: <tools>` / `tool not found: <tools>` | Wrong tool names in `hub.json`. |
| `check tool not exposed: <tool>` | The `CHECKS` tool in `scripts/smoke.py` is not allowlisted. |
| `<tool> failed` | The child is up but can't reach its upstream service; check its account. |
| `smoke=FAIL (<type>: Hub config is invalid …)` | `hub.json` or a child config is invalid; see `config.load.failed` in the log. |

A child without a `CHECKS` entry gets `PASS  N tools, connected (no read-only check in CHECKS)`. Exit code 0 means all PASS. Tool responses and error texts are never printed. Full details are in `logs/mcp-hub.jsonl`.

## Where to look

| Need | File |
|------|------|
| Rules for agents | [AGENTS.md](AGENTS.md) |
| Connect an AI client (Antigravity, Claude Code, Cursor, …) to the Hub | [docs/connecting-a-client.md](docs/connecting-a-client.md) |
| Per-client config: MCP entry, always-on rule, `/mcp-hub` command | [docs/clients/](docs/clients/) |
| Rules a client's agent follows when using the Hub (`mcp-hub:<server-id> <task>`) | [docs/client-rules.md](docs/client-rules.md) |
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
- A child receives only the MCP SDK's default safe variables (e.g. `PATH`, `APPDATA`, `USERNAME`; on POSIX, `HOME`, `USER`), its `inheritEnv` variables, and its configured `env`.
- stdout is reserved for MCP protocol frames.

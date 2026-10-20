# mcp-hub

Local, config-driven MCP gateway for AI agents. The Hub launches child MCP servers over stdio, discovers their tools, exposes only allowlisted ones, routes calls, and writes audit events to `logs/mcp-hub.jsonl`.

**Agents: read [AGENTS.md](AGENTS.md) first.** Its rules are mandatory.

## Run

Python 3.12 is the project runtime. The launcher prepares the local prerequisites before starting.

Run directly, including when an AI client starts the Hub over stdio:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start.ps1
```

On Linux or macOS:

```sh
bash scripts/start.sh
```

If `uv` is missing, the launcher installs it under the ignored `.tools/` directory without changing the global `PATH`. It then installs Python from `.python-version` when needed and runs `uv run --locked`, which creates or synchronizes the project environment from `uv.lock`. The first run needs internet access and write access to the repository and uv-managed data/cache directories. If download or installation fails, the launcher exits with a diagnostic on stderr before starting the Hub. Stdout stays reserved for MCP protocol traffic.

To connect an AI client, paste this into the agent (replace `<MCP_HUB_PATH>` with this repo's absolute path). Other prompts (instructions only, rule refresh, daily use) are in [docs/connecting-a-client.md §0](docs/connecting-a-client.md#0-for-the-human-the-prompts).

```text
Read <MCP_HUB_PATH>/docs/connecting-a-client.md and follow every step to connect yourself to mcp-hub,
using the file for this client in <MCP_HUB_PATH>/docs/clients/: register one MCP server named mcp-hub,
replace any old entries for its child MCPs, install the always-on rule and the /mcp-hub command.
Ask me before writing global config or removing entries, and report what you changed at the end.
```

Daily use after that: `/mcp-hub <server-id> <task>` (Codex: `$mcp-hub …`), e.g. `/mcp-hub mcp-server-logs show the prod logs for service orders-api`. Without the command installed, type `mcp-hub:<server-id> <task>`.

The Hub reads `config/private/hub.json` (or `MCP_HUB_CONFIG`, which must point inside `config/private/`). Child repositories sit next to this repo and are referenced as `../<repo>`.

## Default setup

The default children are mapped in [config/hub.example.json](config/hub.example.json) and [config/mcps/](config/mcps/). Every template uses `../<repo>` paths, so clone all four repos into one parent folder, keeping the repo names as the folder names:

```text
<parent>/
  mcp-hub/
  mcp-server-logs/
  mcp-jira-sync/
  mcp-pr-companion/
```

```powershell
cd <parent>
foreach ($repo in "mcp-hub", "mcp-server-logs", "mcp-jira-sync", "mcp-pr-companion") { git clone "<GIT_BASE>/$repo.git" }
cd mcp-hub
New-Item -ItemType Directory -Force config/private/mcps | Out-Null
Copy-Item config/hub.example.json config/private/hub.json
foreach ($id in "mcp-server-logs", "mcp-jira-sync", "mcp-pr-companion") { Copy-Item "config/mcps/$id.example.json" "config/private/mcps/$id.json" }
```

| Server ID | Repo | Exposed tools | Where the user fills credentials | Contract |
|---|---|---|---|---|
| `mcp-server-logs` | `../mcp-server-logs` | `logs__*` | The child's `config/openobserve.<env>.env` and `config/seq.env` | [docs/mcps/mcp-server-logs.md](docs/mcps/mcp-server-logs.md) |
| `mcp-jira-sync` | `../mcp-jira-sync` | `jira__*` | `<SET_BY_USER>` fields in `config/private/mcps/mcp-jira-sync.json` | [docs/mcps/mcp-jira-sync.md](docs/mcps/mcp-jira-sync.md) |
| `mcp-pr-companion` | `../mcp-pr-companion` | `pr__*` | The child's `.env` | [docs/mcps/mcp-pr-companion.md](docs/mcps/mcp-pr-companion.md) |

Then set up each child repo per its own README, fill its credentials, set `"enabled": true` for its server and feature in `config/private/hub.json`, and run `uv run python scripts/smoke.py`. Everything starts disabled. The `mcp-jira-sync` template runs on this repo's venv (`../mcp-hub/.venv/Scripts/python.exe`); on macOS/Linux set `command` to `../mcp-hub/.venv/bin/python` with `scripts/private_config.py set`.

## Check connections

```powershell
uv run python scripts/smoke.py
```

It starts the Hub as an MCP host would and prints one line per enabled child MCP:

```text
mcp-jira-sync  PASS  5 tools, jira__check_connection ok
mcp-server-logs   PASS  12 tools, logs__list_openobserve_organizations dev=ok stg=ok prod=ok
```

A `FAIL` line names the cause:

| Output | Meaning |
|---|---|
| `not connected: <event> (<error_type>)` | The child didn't start or didn't answer; check `cwd`/`command`. |
| `not connected (no Hub events; see logs)` | No events from this run (rare, e.g. log rotation); rerun. |
| `no exposed tools: <tools>` / `tool not found: <tools>` | Wrong tool names in `hub.json`. |
| `check tool not exposed: <tool>` | The `CHECKS` tool in `scripts/smoke.py` is not allowlisted. |
| `<tool> <env>=failed` | The child is up but can't reach that upstream environment; check its local account file. |
| `smoke=FAIL (<type>: Hub config is invalid …)` | `hub.json` or a child config is invalid; see `config.load.failed` in the log. |

A child without a `CHECKS` entry gets `PASS  N tools, connected (no read-only check in CHECKS)`. Exit code 0 means all PASS. Tool responses and error texts are never printed. Full details are in `logs/mcp-hub.jsonl`.

## Package QC

Run the offline package checks before using or releasing a checkout:

```powershell
uv run --locked --python 3.12 python scripts/package_qc.py
```

The command runs unit tests, the leak checker, and Python compile checks. It does not contact child MCPs or upstream services. Add `--live-smoke` only when private Hub configuration and required child credentials are available. See [Packaging and QC](docs/packaging-and-qc.md) for bootstrap behavior and CI details.

## Where to look

| Need | File |
|------|------|
| Rules for agents | [AGENTS.md](AGENTS.md) |
| Connect an AI client (Antigravity, Claude Code, Cursor, …) to the Hub | [docs/connecting-a-client.md](docs/connecting-a-client.md) |
| Per-client config: MCP entry, always-on rule, `/mcp-hub` command | [docs/clients/](docs/clients/) |
| Rules a client's agent follows when using the Hub (`mcp-hub:<server-id> <task>`) | [docs/client-rules.md](docs/client-rules.md) |
| Add or reconfigure a child MCP | [docs/adding-a-child-mcp.md](docs/adding-a-child-mcp.md) |
| Change child MCP code and keep it compatible with the Hub | [docs/child-mcp-compatibility.md](docs/child-mcp-compatibility.md) |
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

- `config/private/`, `.local/`, `.env`, `.env.*`, and `logs/` are gitignored and never committed.
- Tool arguments and successful results are not logged. Configured values, inherited environment values, and values under sensitive config keys are redacted from Hub error logs.
- A child receives only the MCP SDK's default safe variables (e.g. `PATH`, `APPDATA`, `USERNAME`; on POSIX, `HOME`, `USER`), its `inheritEnv` variables, and its configured `env`.
- stdout is reserved for MCP protocol frames.

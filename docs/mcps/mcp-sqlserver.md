# SQL Server MCP contract

- Hub implementation guidance: [child MCP compatibility contract](../child-mcp-compatibility.md).

- **Repository:** sibling `../mcp-sqlserver` (C#, .NET 10), maintained as a separate repository from `mcp-hub`. The Hub detects this supported sibling automatically and starts it even when the Hub's `mssql` section is empty; the child then uses its own `dbconfig.json`.
- **Purpose:** look at real data to find the root cause of an issue, next to the logs, source, ticket and pull-request MCPs. It is **read-only by design**: only one validated `SELECT` ever reaches the server, inside a transaction that is always rolled back. It cannot insert, update, delete, truncate, create or execute anything.
- **Transport:** local stdio process launched by the Hub as `publish/McpSqlServer.exe serve` in the child repo (`config/private/mcps/mcp-sqlserver.json`, template `config/mcps/mcp-sqlserver.example.json`). On Windows, `inheritEnv` includes `PATHEXT`, `APPDATA`, `LOCALAPPDATA`, `ProgramFiles` and `ProgramFiles(x86)`: NuGet restore fails without a `ProgramFiles` variable.
- **Build:** the `prepare` step runs the child's `scripts/prepare.ps1` (needs the .NET 10 SDK, installed by `preflight --install`). It publishes `publish/McpSqlServer.exe` only when it is missing or older than `src/`, so it is quick when nothing changed. Unlike the Node children, the executable is not rebuilt when the child starts: after a `git pull` of the child, run `hubctl restart`. A stale build logs a warning to the child's stderr at start. If the executable is running outside the Hub (a client configured to start it directly), `prepare` fails and keeps the existing build.
- **Configuration:** follows [Configuration sources](../child-mcp-compatibility.md#configuration-sources). `env.CENTRAL_CONFIG_PATH` points at the Hub child file; the account lives in its `mssql` section: `server`, `port`, `username`, `password`, `database`, `encrypt`, `trust_server_certificate`, `allowed_databases`, `isolation`, `max_rows` (all keys: the child's `docs/CONFIGURATION.md`). Environment `MSSQL_*` wins over the Hub section, which wins over the child's local `dbconfig.json`. The user sets the values; never ask for them in chat.
- **Source report:** `sql__check_connection` returns `config_source` with `env`, `hub`, `local`, `default` or `unset` for each group (server, credentials, limits), never the values.

## Tools

All tools are read-only (`readOnlyHint=true`). Results are one text block of compact JSON; a failure sets the error flag.

- `sql__check_connection`: connectivity, server version, `write_capable`, and where each config group came from (`config_source`: `env|hub|local|default|unset`). Read-only. Input: none. Output: `{success, connected, elapsed_ms, server_version, write_capable, warnings[], config_source{}}`.
- `sql__list_databases`: databases the MCP may query. Input: none. Output: `{databases[{name, state, has_access, is_system}], ...}`.
- `sql__list_tables`: tables and views with approximate row counts. Input: `database`, optional `schema`, `name_like`, `limit`.
- `sql__describe_table`: columns, foreign keys in both directions, indexes, triggers. Input: `database`, `table`.
- `sql__find_columns`: which tables hold a column. Input: `name_like`, optional `database` (omit to search all allowed databases), `limit`.
- `sql__lookup_rows`: rows of one table without writing SQL. Input: `database`, `table`, optional `filters[{column, op, value}]`, `order_by`, `columns`, `limit`. Output includes the generated `sql`.
- `sql__get_related_rows`: one row and its parent/child rows through foreign keys. Input: `database`, `table`, `key_value`, optional `key_column`, `depth` (1-2), `rows_per_relation`.
- `sql__run_select`: one free-form `SELECT`. Input: `database`, `query`, optional `max_rows`. Anything that is not a single SELECT is rejected before reaching the server.
- `sql__explain_select`: estimated execution plan, not executed. Input: `database`, `query`.
- `sql__get_object_definition`: source of a view, procedure, function or trigger. Input: `database`, `name`.
- `sql__search_context`, `sql__get_object_context`, `sql__check_schema_drift`: search and read the child's offline schema snapshot (`ai-context/`, built by the child's command line `scan`) and compare it with the live databases. Without a snapshot, or for an object that is not in it, they return an error result; zero search matches is a success.

Bounds: rows 1-1000 (default 100), 2,000 characters per cell, 200,000 characters per response. Columns named like `password`, `token`, `secret`, `salt`, `hash`, `api_key` are returned as `***`. Check `truncated` and filter by key or date instead of reading whole tables.

## Hub result cache

Shared rules: [Hub result cache](README.md#hub-result-cache). Defaults here: 60 s for `sql__check_connection`; 600 s for catalog tools (`list_databases`, `list_tables`, `describe_table`, `find_columns`, `get_object_definition`); 120 s for offline snapshot reads (`search_context`, `get_object_context`); 300 s for `sql__check_schema_drift`; **none** for `sql__lookup_rows`, `sql__get_related_rows`, `sql__run_select` and `sql__explain_select`, because the data changes while an issue is investigated. Never repeat an identical data query within a task.

## Care

- Start with `sql__list_databases` (or use `hub__plan` SQL hints when configured; call `sql__check_connection` only after a failed call or with `connection_checks: true`). Prefer `sql__search_context` / `sql__find_columns` / `sql__describe_table` to learn the schema before reading data, then `sql__lookup_rows` or `sql__get_related_rows`; use `sql__run_select` only for what they cannot express.
- Results are **real business data**. Do not copy personal data into tickets, pull requests or logs; quote only what the finding needs.
- `write_capable: true` is a warning about the login, not about this MCP; it still cannot write. Ask the owner for the read-only login described in the child's `docs/readonly-login.sql`.
- Do not use this MCP's tools in a Hub job specialist unless a `sql` role has been added deliberately: specialist tool results go to the configured model provider.
- A query on production runs with `READ UNCOMMITTED` and a 3 s lock timeout by default; large unfiltered scans can still be slow. Use `sql__explain_select` first.

## Hub integration

- Read-only smoke check: `sql__check_connection`; success means `{"connected": true, ...}`. Without an account it answers `not configured`, and the smoke check prints `WARN`; any other failure is a `FAIL`. If its output changes, update `scripts/smoke.py` and `tests/test_smoke.py`.
- Compatibility notes: tool names, the `mssql` section and `CENTRAL_CONFIG_PATH` are the contract. The child's own `docs/HUB_INTEGRATION.md` and `docs/TOOLS.md` describe the details; a change of a tool name or key must change this file, `config/hub.example.json` and `scripts/smoke.py` together.

## Diagnostics

- Child logs: `../mcp-sqlserver/logs/process.log` and `error.log` (never contain SQL text, parameters or rows). Hub events: `logs/mcp-hub.jsonl` (search by request ID); the child's stderr is in `logs/children/mcp-sqlserver.log`.
- `not configured`: fill the `mssql` section (`server`, `username`, `password`) and restart the client that runs the Hub.
- `Database 'x' is not allowed`: add it to `allowed_databases` (system databases are refused by default).
- `The query timed out` / `Lock request timed out`: filter by key or date, or check for a blocking session.
- `Login failed`, `Cannot connect`, TLS certificate error: wrong credentials, address/firewall, or set `trust_server_certificate`.

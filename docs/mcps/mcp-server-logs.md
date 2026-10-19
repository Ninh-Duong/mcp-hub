# Server Logs MCP contract

- Hub implementation guidance: [child MCP compatibility contract](../child-mcp-compatibility.md).

- Repository: sibling `../mcp-server-logs` (Node 22+). Its own rules: `../mcp-server-logs/AGENTS.md`.
- Transport: local stdio process launched by the Hub as `node start.mjs` in the child repo (`config/private/mcps/mcp-server-logs.json`, template `config/mcps/mcp-server-logs.example.json`). `start.mjs` runs `npm ci` / `npm run build` when needed, so the first start can take a minute; `inheritEnv` includes `APPDATA`, `LOCALAPPDATA`, `PATHEXT`, and `COMSPEC` for npm on Windows.
- Configuration: the Hub passes no secrets. The child reads its accounts from its own `config/openobserve.<dev|stg|prod>.env` and `config/seq.env` (`OPENOBSERVE_URL`, `OPENOBSERVE_EMAIL`, `OPENOBSERVE_TOKEN`, `SEQ_SERVER_URL`, `SEQ_API_KEY`). The user fills those files; agents never read them. Optional `env.OPENOBSERVE_ENV` sets the default environment (`dev` otherwise).
- `timeoutSeconds` is 600 because exports can take minutes.

## Tools

All are read-only except `logs__export_openobserve_logs`. The OpenObserve tools, `logs__query_logs`, and the export tool take an optional `env` (`dev`, `stg`, `prod`). Use the environment the user names and keep it for the whole investigation.

- `logs__list_log_providers`: configured backends and which OpenObserve environments have an account. Reads config only.
- `logs__list_openobserve_organizations`: connects to OpenObserve and lists organizations. Use it for smoke tests.
- `logs__list_openobserve_streams`, `logs__list_openobserve_stream_schema`, `logs__list_openobserve_services`: discovery.
- `logs__query_logs`: Seq filter or one read-only OpenObserve `SELECT`. Takes `provider`, `organization`, `stream`, `from`/`to` (ISO or `15m`/`1h`/`24h`/`7d`), `limit` (1–500), and `level`.
- `logs__search_openobserve_logs`, `logs__get_openobserve_service_logs`, `logs__find_openobserve_logs_by_rcid`: live searches.
- `logs__export_openobserve_logs`: **writes** one encrypted export folder under `../mcp-server-logs/ai-context/`.
- `logs__list_ai_context_snapshots`, `logs__read_ai_context`: the only way to read exports.

## Care

- Read ai-context exports **only** through `logs__list_ai_context_snapshots` → `logs__read_ai_context`. Never open, copy, or decrypt `.lhub` files or anything under `ai-context/` with file tools or scripts; the access check runs inside the tool.
- Reading order: `SUMMARY.lhub` (Bugs table, top patterns with file:line) → `read_ai_context(file, offset)` at that line in `services/<service>/<level>.lhub` → the failing request in `rcid/<field>-<id>.lhub`, or grep an rcid across files.
- `services/` leaves out framework noise (health checks, ASP.NET pipeline, EF Core SQL, HttpClient, gRPC…). A request's `rcid/` file has all of it, so never conclude "no SQL/HTTP activity" from `services/` alone.
- "No access" means the OpenObserve account for that env is missing or not enabled for the organization. Tell the user; do not look for another way to read the data.
- Diagnose by comparing the logs with the source code of the workspace you are in, and cite log lines and source files.
- The child's own prompts (`investigate_api_bug`, `analyze_ai_context`) are not exposed through the Hub; this contract is the Hub's `mcp-server-logs` prompt.
- Never write log content or organization identifiers into this repo's tracked files.

## Hub integration

- Read-only smoke check: `logs__list_openobserve_organizations` runs with `env: dev`, `stg`, and `prod`; every result must be JSON with an `organizations` array. A smoke PASS means all three accounts reached OpenObserve. If the result shape changes, update `scripts/smoke.py` and `tests/test_smoke.py`.
- Preserve the child tool names and read/write annotations; the Hub exposes only the aliases allowlisted in `config/private/hub.json`.

## Diagnostics

- Child logs: `../mcp-server-logs/logs/mcp-process.log`. Hub events: `logs/mcp-hub.jsonl`.
- Child self-check: `node start.mjs --preflight` in the child repo.
- End-to-end: `uv run python scripts/smoke.py` (checks `logs__list_openobserve_organizations` for all three OpenObserve environments).

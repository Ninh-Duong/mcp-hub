# Server Logs MCP contract

- Hub implementation guidance: [child MCP compatibility contract](../child-mcp-compatibility.md).

- Repository: sibling `../mcp-server-logs` (Node 22+). Its own rules: `../mcp-server-logs/AGENTS.md`.
- Hub discovers this supported sibling automatically from the relative path. No private Hub config is needed unless overriding or disabling it; `hub__status` reports detection and connection.
- Transport: local stdio process launched by the Hub as `node start.mjs` in the child repo (`config/private/mcps/mcp-server-logs.json`, template `config/mcps/mcp-server-logs.example.json`). `start.mjs` runs `npm ci` / `npm run build` when needed, so the first start can take a minute; `inheritEnv` includes `APPDATA`, `LOCALAPPDATA`, `PATHEXT`, and `COMSPEC` for npm on Windows.
- Configuration: the Hub can pass the accounts. `env.CENTRAL_CONFIG_PATH` points the child at this Hub's `config/private/mcps/mcp-server-logs.json`, whose `openobserve` (`default_env`, `dev`/`stg`/`prod` = `{url, email, token}`) and `seq` (`server_url`, `api_key`) sections the user fills by hand (`scripts/private_config.py set` refuses real email/token values; agents never read them). Leave a section empty to keep the child's own files. Order per value: env vars set for the child → the Hub file → the child's `config/openobserve.<dev|stg|prod>.env` and `config/seq.env` (`OPENOBSERVE_URL`, `OPENOBSERVE_EMAIL`, `OPENOBSERVE_TOKEN`, `SEQ_SERVER_URL`, `SEQ_API_KEY`), which the child creates from its examples on first start. Never use `<SET_BY_USER>` in these sections: it marks the child "credentials required". A missing or broken Hub file is reported on the child's stderr and the child falls back to its own files. Source report: `logs__list_log_providers` gives `source` (`env`, `hub`, `local`, `unset`) for the Seq provider and for each item of `openobserve_environments`; `node start.mjs --preflight` shows the same per row. Default environment: `env.OPENOBSERVE_ENV`, else an account from `OPENOBSERVE_URL`/`EMAIL`/`TOKEN` env vars, else `openobserve.default_env`, else `dev` if it has a URL, else none. A root `.env` in the child repo, or `LOG_DIR` / `CONFIG_DIR` / `AI_CONTEXT_DIR`, can move its logs, account files and exports; keep the defaults under the Hub.
- First use: run `node start.mjs --preflight` once in the child repo so the first Hub start does not spend its time on install/build.
- `timeoutSeconds` is 600 because exports can take minutes.

## Tools

All are read-only except `logs__export_openobserve_logs`. The OpenObserve tools, `logs__query_logs`, and the export tool take an optional `env` (`dev`, `stg`, `prod`); `logs__list_ai_context_snapshots` takes `env` only as a filter, and `logs__read_ai_context` takes none. Use the environment the user names and keep it for the whole investigation.

- `logs__list_log_providers`: configured backends and which OpenObserve environments have an account, each with its config `source`. Reads config only.
- `logs__list_openobserve_organizations`: connects to OpenObserve and lists organizations. Use it for smoke tests and to explain a failed call, not as a warm-up before every query.
- `logs__list_openobserve_streams`, `logs__list_openobserve_stream_schema`, `logs__list_openobserve_services`: discovery. The child caches stream lists and schemas in process for 5 minutes.
- `logs__query_logs`: Seq filter or one read-only OpenObserve `SELECT`. Takes `provider`, `organization`, `stream`, `query`, `from`/`to` (each ISO or relative `N` + `m`/`h`/`d`/`w`, e.g. `15m`, `7d`), `limit` (1–500), and `level`. Without `provider` it uses the configured default provider; it does not fan out to every backend. Output: JSON `{total, query, provider, logs}`.
- `logs__search_openobserve_logs`: OpenObserve SQL search. Output: JSON `{total, logs, search}`.
- `logs__get_openobserve_service_logs`, `logs__find_openobserve_logs_by_rcid`: per-service and per-request searches; they query OpenObserve on each call (the Hub result cache absorbs short repeats). Output: JSON `{total, logs}`.
- `logs__export_openobserve_logs`: **writes** one encrypted export folder `ai-context/openobserve/<env>/<org>/<stream>/<folder>/` in the child repo (built as `<folder>_partial`, renamed when done) and adds a line to `ai-context/INDEX.md`.
- `logs__list_ai_context_snapshots`, `logs__read_ai_context`: the only way to read exports. `logs__read_ai_context` returns plain text (`limit` 1–1000, default 200) and checks OpenObserve access (cached 60 s).

## Caching (contract the child must meet)

`../mcp-server-logs` must implement these behaviors; agents may rely on them.

- SQL searches (`logs__search_openobserve_logs`, and `logs__query_logs` on OpenObserve) look up the ai-context cache first, keyed by normalized SQL + environment + organization + absolute time window, and call OpenObserve only on a miss. Empty results are not cached. A larger `limit` than a cached search held is a miss unless that search returned every row.
- `logs__search_openobserve_logs` output adds `cached` (true or false) and `window: {from, to}`, the bounds actually used.
- Relative windows (`15m`) and the default end count from the start of the current minute, so repeats within the same minute share a key. Rows from the last minute appear from the next minute on; pass ISO bounds for an exact window. A cached result for a recent window does not include rows that arrived later for that same window.
- Stream lists and stream schemas are cached in process for 5 minutes, so a new stream or field can take up to 5 minutes to appear.
- `logs__query_logs` without `provider` queries one provider: OpenObserve when its account for the call's env is configured, else Seq. Its output `provider` names the provider used. A provider error is a tool error, and no configured provider is an error. With OpenObserve as the default, `organization` is required; pass `provider: "seq"` to query Seq.
- Service-log and request-ID searches use the same rounded window for their cache key and upstream search. For a request already exported, read its `rcid/` file through `logs__read_ai_context`.

## Hub result cache

The Hub keeps successful results of a tool in memory for that tool's `cacheSeconds` (set per tool in `config/hub.example.json`, overridable in `config/private/hub.json`; `0` or omitted means no caching). It applies a TTL only when the child declares `readOnlyHint=true`, so write tools are never cached. The key is the tool plus its canonical arguments; error results are never stored, and concurrent identical calls share one child call. A cached answer is logged as `tool.call.cached`. When one of a child's write tools succeeds, the Hub drops that child's cached results, so a read after a sync or export is fresh.

Defaults here: 300 s for `logs__list_openobserve_organizations`; 120 s for `logs__list_log_providers`, the stream/schema/service discovery tools, and `logs__list_ai_context_snapshots`; 60 s for `logs__query_logs`, `logs__search_openobserve_logs`, `logs__get_openobserve_service_logs`, `logs__find_openobserve_logs_by_rcid`, and `logs__read_ai_context`; none for `logs__export_openobserve_logs`.

## Care

- Cache first: `logs__list_ai_context_snapshots` → `logs__read_ai_context` before any remote search. Query OpenObserve or Seq only when no snapshot covers the service and window, or the user asks for fresh data. Never repeat an identical search within a task.
- Read ai-context exports **only** through `logs__list_ai_context_snapshots` → `logs__read_ai_context`. Never open, copy, or decrypt `.lhub` files or anything under `ai-context/` with file tools or scripts; the access check runs inside the tool.
- Reading order: `SUMMARY.lhub` (Bugs table, top patterns with file:line) → `read_ai_context(file, offset)` at that line in `services/<service>/<level>.lhub` → the failing request in `rcid/<field>-<id>.lhub`, or grep an rcid across files.
- `services/` leaves out framework noise (health checks, ASP.NET pipeline, EF Core SQL, HttpClient, gRPC…). A request's `rcid/` file has all of it, so never conclude "no SQL/HTTP activity" from `services/` alone.
- "No access" means the OpenObserve account for that env is missing or not enabled for the organization. Tell the user; do not look for another way to read the data.
- Diagnose by comparing the logs with the source code of the workspace you are in, and cite log lines and source files.
- The child's own prompts (`investigate_api_bug`, `analyze_ai_context`) are not exposed through the Hub; this contract is the Hub's `mcp-server-logs` prompt.
- Never write log content or organization identifiers into this repo's tracked files.

## Hub integration

- Read-only smoke check: `logs__list_log_providers` first; each environment it reports with `configured: false` is shown as `unset` and skipped. `logs__list_openobserve_organizations` then runs for each remaining `env` (`dev`, `stg`, `prod`); every result must be JSON with an `organizations` array. A smoke PASS means every configured account reached OpenObserve, and at least one is configured. If the result shape changes, update `scripts/smoke.py` and `tests/test_smoke.py`.
- Preserve the child tool names and read/write annotations; the Hub exposes only the aliases allowlisted in `config/hub.example.json` plus overrides in `config/private/hub.json`.

## Diagnostics

- Child logs: `../mcp-server-logs/logs/mcp-process.log`. Hub events: `logs/mcp-hub.jsonl`.
- Child self-check: `node start.mjs --preflight` in the child repo.
- End-to-end: `uv run python scripts/smoke.py` (checks `logs__list_openobserve_organizations` for each configured OpenObserve environment).

# Child MCP contracts

One `<server-id>.md` per child MCP. The Hub serves the file as the child's MCP prompt, alongside the list of that child's allowlisted Hub tools, so write it for an agent that is about to call those tools. Use repo-relative paths and env/config key names only, never values.

When changing child source, follow the Hub's shared [child MCP compatibility contract](../child-mcp-compatibility.md) and the child repo's own agent instructions. This page is the contract template for how agents use one specific child; it is not a substitute for that implementation guidance.

## Hub result cache

These rules apply to every child; each contract lists only its own `cacheSeconds` defaults.

- The Hub keeps a successful result in memory for the tool's `cacheSeconds` (per tool in `config/hub.example.json`, overridable in `config/private/hub.json`; `0` or omitted means no caching), up to 256 entries and 4 MB in total.
- A TTL applies only to tools the child marks `readOnlyHint=true`; other tools are never cached.
- The key is the tool plus its canonical JSON arguments. Error results, including text that starts with `Error:` or a cross-mark symbol, are never stored, and concurrent identical calls share one child call.
- A cached answer is logged as `tool.call.cached`. `hub__status` reports `result_cache` hits, misses, and entries.
- Calling one of a child's tools that is not read-only, whatever its outcome, drops that child's cached results, so a read after a sync or export is fresh.
- Cache first: read cached child data (`jira__read_ticket`, `logs__list_ai_context_snapshots`, `pr__list_synced_prs`) before syncing, and sync only when it is missing, stale, or the user asks (see [client-rules.md](../client-rules.md)).

## Template

```markdown
# <Name> MCP contract

- Repository: sibling `../<repo>`.
- Transport: local stdio process launched by the Hub (`config/private/mcps/<server-id>.json`).
- Configuration: env `<VAR_NAME>`, ... ; private keys `<block>.<key>`, ... (values set by the user).

## Tools

- `<exposed_name>`: <what it does>. Read-only | Writes <what, where>. Input: <key args>. Output: <shape>.

## Hub result cache

Shared rules: [Hub result cache](README.md#hub-result-cache). Defaults here: <`cacheSeconds` per tool in `config/hub.example.json`; 0 for writes and for data the child must not cache in memory>.

## Care

- <tools to avoid for smoke tests, rate limits, destructive operations, data boundaries>

## Hub integration

- Read-only smoke check: `<exposed_name>`; success means <expected response shape or safe success marker>.
- Compatibility notes: <stable tool names, required config names, or constraints the child developer must preserve>.

## Diagnostics

- Child logs: `../<repo>/<log path>`. Hub events: `logs/mcp-hub.jsonl` (search by request ID).
- <common errors and their fix>
```

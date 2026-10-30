# Child MCP contracts

One `<server-id>.md` per child MCP. The Hub serves the file as the child's MCP prompt, alongside the list of that child's allowlisted Hub tools, so write it for an agent that is about to call those tools. Use repo-relative paths and env/config key names only, never values.

When changing child source, follow the Hub's shared [child MCP compatibility contract](../child-mcp-compatibility.md) and the child repo's own agent instructions. This page is the contract template for how agents use one specific child; it is not a substitute for that implementation guidance.

## Template

```markdown
# <Name> MCP contract

- Repository: sibling `../<repo>`.
- Transport: local stdio process launched by the Hub (`config/private/mcps/<server-id>.json`).
- Configuration: env `<VAR_NAME>`, ... ; private keys `<block>.<key>`, ... (values set by the user).

## Tools

- `<exposed_name>`: <what it does>. Read-only | Writes <what, where>. Input: <key args>. Output: <shape>.

## Hub result cache

- `cacheSeconds` per tool in `config/hub.example.json` (read-only tools only; 0 for writes and for data the child must not cache in memory). Cached answers are logged as `tool.call.cached`.

## Care

- <tools to avoid for smoke tests, rate limits, destructive operations, data boundaries>

## Hub integration

- Read-only smoke check: `<exposed_name>`; success means <expected response shape or safe success marker>.
- Compatibility notes: <stable tool names, required config names, or constraints the child developer must preserve>.

## Diagnostics

- Child logs: `../<repo>/<log path>`. Hub events: `logs/mcp-hub.jsonl` (search by request ID).
- <common errors and their fix>
```

# Child MCP contracts

One `<server-id>.md` per child MCP. The Hub serves the file as the child's MCP prompt, alongside the list of that child's allowlisted Hub tools, so write it for an agent that is about to call those tools. Use repo-relative paths and env/config key names only, never values.

## Template

```markdown
# <Name> MCP contract

- Repository: sibling `../<repo>`.
- Transport: local stdio process launched by the Hub (`config/private/mcps/<server-id>.json`).
- Configuration: env `<VAR_NAME>`, ... ; private keys `<block>.<key>`, ... (values set by the user).

## Tools

- `<exposed_name>`: <what it does>. Read-only | Writes <what, where>. Input: <key args>. Output: <shape>.

## Care

- <tools to avoid for smoke tests, rate limits, destructive operations, data boundaries>

## Diagnostics

- Child logs: `../<repo>/<log path>`. Hub events: `logs/mcp-hub.jsonl` (search by request ID).
- <common errors and their fix>
```

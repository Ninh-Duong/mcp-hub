# Architecture

## Responsibilities

- **Hub config** declares child MCP connection details, enabled features, and the tool allowlist.
- **Per-MCP private config** holds local settings or references to credentials needed by one child MCP.
- **Hub runtime** validates config, connects to child MCPs, exposes allowlisted tools, routes calls, and writes audit events.
- **Child MCP** owns its domain logic, credentials, and upstream service behavior.
- **Agent docs** explain tool purpose and operational rules; they do not enforce access control. The Hub must enforce the configured allowlist.

## Request path

`Agent → Hub tool allowlist → child MCP client → child MCP tool → Hub audit log`

Every exposed tool should map to exactly one child server and tool name. Use namespaced exposed names such as `serverId__toolName` to avoid collisions. A feature may expose tools from multiple child MCPs. Keep the initial design as a curated proxy; add cross-server composite tools only when a real workflow needs them.

## Failure handling and child selection

- A child that fails to start, or a configured tool the child does not list, is logged (`server.connect.failed`, `route.skipped`) and skipped; the Hub starts with the remaining tools and fails only when none are available.
- Each call is limited by the child's `timeoutSeconds` (default 60); a timeout is logged as `tool.call.failed` with `error_type=TimeoutError`.
- The Hub exposes one MCP prompt per connected child, named after its server ID. Clients that support prompts show it as a slash command (the exact format is client-defined); it tells the Agent which Hub tools belong to that child and includes `docs/mcps/<server-id>.md`.
- On connect, the Hub sends server instructions, essentials first: a summary under 512 characters (`INSTRUCTIONS_SUMMARY` in `server.py`), the tool list per child, and then [client-rules.md](client-rules.md), read at startup. Clients may truncate them (Claude Code cuts at 4,096 characters), so only the tail of the rules can be lost. Clients that ignore server instructions get the same rules from the always-on rule installed by [connecting-a-client.md](connecting-a-client.md) step 7.

## Local child servers

Children run over `stdio`. The Hub launches each one with an argument array (never a shell string) in the child's `cwd`, passing only the MCP SDK's default safe variables, the `inheritEnv` names, and the child's `env` values. Child repositories are expected to sit next to this repo, so configs reference them as `../<repo>`.

Each child MCP has a contract at `docs/mcps/<server-id>.md`; see [mcps/README.md](mcps/README.md). To add one, follow [adding-a-child-mcp.md](adding-a-child-mcp.md).

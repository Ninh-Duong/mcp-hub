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

## Local child servers

The example config uses `stdio` and a repository path supplied by an environment variable. Launch a child process with an argument array, not a shell-built command string. The Hub should pass only the environment variables that server needs.

Each child MCP gets a contract at `docs/mcps/<server-id>.md` describing its purpose, tools, read/write effects, required configuration variable names, and known errors.

# Runtime source layout

The runtime implementation will be split by responsibility after selecting the language and MCP SDK:

- `config/` — load and validate the hub manifest and private per-MCP settings.
- `clients/` — connect to and manage child MCP processes/connections.
- `routing/` — expose allowlisted tools and route calls to the owning child server.
- `logging/` — structured audit events, redaction, and log rotation.

Keep credentials out of tool descriptions, source files, and log records.

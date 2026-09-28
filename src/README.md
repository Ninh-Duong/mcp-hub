# Runtime source layout

The Python runtime lives in `src/mcp_hub/`:

- `config.py` — validates the Hub manifest and private per-server configs.
- `server.py` — starts child MCP stdio clients, exposes allowlisted schemas, routes calls, and records audit events.
- `audit.py` — writes rotating JSONL logs with secret redaction.

The Hub uses the MCP Python SDK low-level server so downstream JSON Schemas and tool annotations can be forwarded without hand-written wrappers.

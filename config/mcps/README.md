# Per-MCP local configuration

Create one local file at `config/private/<server-id>.json` for each child MCP that needs configuration. Copy the matching safe template from this directory and follow that MCP's documentation.

Keep actual credential values in environment variables or a local secret store where possible. If a child MCP requires a credential in a file, keep that file under `config/private/`; it is ignored by Git. Do not put real credentials in `*.example.json` files.

The Hub runtime must never log the contents of these files or return them to the AI agent.

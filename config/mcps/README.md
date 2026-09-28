# Per-MCP local configuration

Create one local file at `config/private/mcps/<server-id>.json` for each child MCP. Copy the matching safe template from this directory and follow that MCP's configuration contract.

Credential values may be stored only in ignored `config/private/` files or resolved by the child from an OS secret store/environment. Never put real values in `*.example.json` files, source code, logs, or tool results. The Hub loads private settings only to configure the child and to redact known secret values from error logs.

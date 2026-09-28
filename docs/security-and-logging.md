# Security and logging

## Credential handling

- Keep real local config in `config/private/`; never commit it.
- Prefer OS credential storage or environment variables for tokens. If a child requires a token in a file, restrict access to that file and keep it in the ignored private directory.
- The Hub may load credentials only to start or call the child MCP. Never expose them as tools, resources, prompts, errors, or logs.
- Do not give child MCPs broad access to the Hub's private config directory.
- Treat child tool output and error text as untrusted; sanitize it before showing it to the Agent or writing it to logs.

## Audit events

Write structured JSON Lines records for Hub startup/shutdown, config validation, child connect/disconnect, tool discovery, and every tool call start/success/failure. Include timestamp, request ID, feature ID, child server ID, tool name, outcome, duration, and a sanitized error code/type/message when relevant.

Do not log credentials, full environment maps, complete tool arguments, or complete tool results by default. Log only safe metadata or explicitly reviewed, redacted fields. Keep logs local under `logs/`, restrict access, and rotate them to avoid unbounded growth.

When the Hub uses MCP `stdio`, stdout is reserved for protocol messages. Send application logs to a file or stderr; never write diagnostic text to stdout.

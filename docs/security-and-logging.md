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

## Event reference (`logs/mcp-hub.jsonl`)

| Event | Level | Meaning |
|-------|-------|---------|
| `config.load.failed` | ERROR | Hub config or a child config is invalid; `error` names the problem. The Hub does not start. |
| `server.connect.failed` | ERROR | Child process didn't start or finish the MCP handshake. That child is skipped. |
| `server.list_tools.failed` | ERROR | Child started but tool listing failed. That child is skipped. |
| `server.connected` | INFO | Child is up; `tools_count` is how many tools it offers. |
| `route.skipped` | WARNING | An allowlisted tool wasn't exposed; `reason` is `server unavailable` or `tool not found`. |
| `hub.tools.registered` | INFO | Final list of exposed tool names. |
| `hub.started` / `hub.stopped` | INFO | Lifecycle. |
| `hub.tools.listed` | INFO | A client listed tools. |
| `tool.call.started` | INFO | Call routed; it carries `request_id`, `feature`, `server`, and `tool`. |
| `tool.call.succeeded` | INFO | Call finished; includes `duration_ms`. |
| `tool.call.failed` | ERROR | Exception, timeout (`error_type=TimeoutError`), or a child error result (`error_summary`, redacted). |
| `tool.call.denied` | WARNING | A client called a tool that isn't allowlisted. |

To diagnose a failure, search for the `request_id` returned in the tool's error text.

# Security and logging

## Credential handling

- Keep real local config in `config/private/`; never commit it.
- Prefer OS credential storage or environment variables for tokens. If a child requires a token in a file, restrict access to that file and keep it in the ignored private directory.
- The Hub may load credentials only to start or call the child MCP. Never expose them as tools, resources, prompts, errors, or logs.
- Hub error-log redaction includes configured child `env` values, the actual values copied from `inheritEnv`, and string values anywhere below a sensitive key in the child config. Key matching includes `api_key`/`api-key`, keys ending in `_key`/`-key` (e.g. `ai_context_key`), tokens, secrets, passwords, authorization, email, and credentials.
- Redaction applies to Hub audit fields `error` and `error_summary`; it does not rewrite successful tool results or child error results forwarded to the agent. Child MCPs must not include credentials in their errors or logs.
- Do not give child MCPs broad access to the Hub's private config directory.
- Child tool output is untrusted. The Hub forwards successful results to the Agent unchanged, so the Agent must treat them as data ([client-rules.md](client-rules.md)). Error text is redacted before it is written to logs. If a call raises (child crash, timeout), the Agent gets only `Child MCP call failed. See Hub log request_id=<n>`. If the child returns an error result, that result reaches the Agent unchanged, and its redacted first line is logged as `error_summary`.

## Audit events

The Hub writes JSON Lines records for startup/shutdown, config validation, child connection, tool discovery, and every tool call. Fields: `timestamp`, `event`, and when relevant `request_id`, `feature`, `server`, `tool`, `duration_ms`, `error_type`, and redacted `error` / `error_summary`. The full list is in the event reference below.

`hub__status` reports only child IDs, counts, and states; it does not expose config paths or credentials. Its calls use the same `tool.call.started` and `tool.call.succeeded` audit events as child tools.

Do not log credentials, full environment maps, complete tool arguments, or complete tool results by default. Log only safe metadata or explicitly reviewed, redacted fields. Keep logs local under `logs/`, restrict access, and rotate them to avoid unbounded growth.

`scripts/check_leaks.py` scans tracked and non-ignored untracked files, including `uv.lock`, for machine paths, non-`example.com` email domains, and `config/private/`, `.local/`, `logs/`, `.env`, or `.env.*` path components. It skips only its own source and reports locations without printing matched text.

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

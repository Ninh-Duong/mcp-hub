# Security and logging

## Credential handling

- Keep real local config in `config/private/`; never commit it.
- The shared-service bearer token is generated in `config/private/service.json`. `scripts/http_headers.ps1` emits a JSON `Authorization` header only to the requesting client helper process; it never writes the token to Hub logs or MCP tool content. The stdio bridge reads the same token in process.
- SQLite stores job IDs, timestamps, states, role/tool names, event metadata, and child tool-call metadata unencrypted. Requests, summaries, pending write arguments, and full child results are protected with Windows user-scope DPAPI before storage. Details and activity records are purged after 30 days. A database copied to another Windows account cannot be decrypted there.
- Agent keys live in `config/private/agents.json`. Job project aliases and allowed environments live in `config/private/projects.json`; the independent source child maps those aliases to local roots in its own ignored `config/private/projects.json`. Status commands report models/readiness or aliases only.
- Prefer OS credential storage or environment variables for tokens. If a child requires a token in a file, restrict access to that file and keep it in the ignored private directory.
- The Hub may load credentials only to start or call the child MCP. Never expose them as tools, resources, prompts, errors, or logs.
- Hub error-log redaction includes configured child `env` values, the actual values copied from `inheritEnv`, and string values anywhere below a sensitive key in the child config. Key matching includes `api_key`/`api-key`, keys ending in `_key`/`-key` (e.g. `ai_context_key`), tokens, secrets, passwords, authorization, email, and credentials.
- Redaction applies to Hub audit fields `error` and `error_summary`; it does not rewrite successful tool results or child error results forwarded to the agent. Child MCPs must not include credentials in their errors or logs.
- Do not give child MCPs broad access to the Hub's private config directory.
- Child tool output is untrusted. The Hub forwards successful results to the Agent unchanged, so the Agent must treat them as data ([client-rules.md](client-rules.md)). Error text is redacted before it is written to logs. If a call raises (child crash, timeout), the Agent gets only `Child MCP call failed. See Hub log request_id=<n>`. If the child returns an error result, that result reaches the Agent unchanged, and its redacted first line is logged as `error_summary`.

## Audit events

The Hub writes JSON Lines records for startup/shutdown, config validation, child connection, tool discovery, and every child or `hub__status` call. Job tools (`hub__submit_issue`, `hub__approve_job`, …) record their state changes in the job store timeline instead. Fields: `timestamp`, `event`, and when relevant `request_id`, `feature`, `server`, `tool`, `duration_ms`, `error_type`, and redacted `error` / `error_summary`. The full list is in the event reference below.

`hub__status` reports only child IDs, counts, and states; it does not expose config paths or credentials. Its calls use the same `tool.call.started` and `tool.call.succeeded` audit events as child tools.

On Windows, `chmod` does not restrict files, and a repo under a shared drive often inherits `Authenticated Users` write access. `hubctl install` therefore limits `config/private/` to the current user and SYSTEM. Keep the checkout itself in a folder other accounts cannot write, because the logon task runs `scripts/start.ps1` from it. Any process running as the same user can read the bearer token and act as the Hub; that is the trust boundary.

The shared HTTP endpoint binds only to `127.0.0.1`, checks `Host` and `Origin`, and requires the local bearer token. `/health` returns only liveness. Dashboard pairing tokens are single-use and short-lived; browser sessions are `HttpOnly`, same-site cookies. The live activity SSE endpoint uses that same paired session and emits only call lifecycle metadata, never tool arguments, results, or error text. HTTP access logs are disabled. Dashboard previews mask secret-like fields before they reach the browser. Pending arguments are encrypted at rest and never included in metadata events.

Before an agent-originated child write runs, the Hub stores the exact tool and arguments, then waits in `awaiting_approval`. The dashboard shows the tool and a redacted argument preview. A user can approve there, or copy a single-use code into an AI client. Approval codes last 10 minutes; the pending write is bounded by the 15-minute job window. Documented encrypted cache updates are allowed during investigation. Direct child calls still follow each child's existing contract and annotations.

Investigation timeline events live in the SQLite job store, not in `logs/mcp-hub.jsonl`. They contain state changes, agent/child tool names, timing, and evidence IDs only. The dashboard's 30-day activity table stores one row per child tool call with child ID, tool name, MCP client label, investigation role when applicable, start/end time, duration, state, and error type. It does not store prompts, arguments, results, or error text. Requests, tool arguments, full tool results, and summaries are stored in protected blobs. Job rows, activity rows, and their timelines are deleted at the 30-day expiry.

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

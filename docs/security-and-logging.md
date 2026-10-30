# Security and logging

## Credential handling

- Keep real local config in `config/private/`; never commit it.
- The shared-service bearer token is generated in `config/private/service.json`. `scripts/http_headers.ps1` emits a JSON `Authorization` header only to the requesting client helper process; it never writes the token to Hub logs or MCP tool content. The stdio bridge reads the same token in process.
- SQLite stores job IDs, timestamps, states, role/tool names, event metadata, and child tool-call metadata unencrypted. Requests, summaries, pending write arguments, and full child results are protected with Windows user-scope DPAPI before storage. Details and activity records are purged after 30 days. A database copied to another Windows account cannot be decrypted there. A job whose summary cannot be decrypted is listed with `summary: null` and `summary_unavailable: true` instead of failing the whole job list; a request that cannot decrypt the job data it needs answers `500` with a fixed message and no details.
- Agent keys live in `config/private/agents.json`. Job project aliases and allowed environments live in `config/private/projects.json`; the independent source child maps those aliases to local roots in its own ignored `config/private/projects.json`. Status commands report models/readiness or aliases only.
- Model providers receive only minimized, redacted job content. OpenAI Responses calls are sent with `store=False`, so OpenAI does not keep the response for later retrieval. Every provider SDK client is created with a 60-second request timeout and an explicit retry limit of 2 (`MAX_RETRIES` in `agents/providers.py`; Gemini counts this as 3 attempts in total).
- Prefer OS credential storage or environment variables for tokens. If a child requires a token in a file, restrict access to that file and keep it in the ignored private directory.
- The Hub may load credentials only to start or call the child MCP. Never expose them as tools, resources, prompts, errors, or logs.
- Hub error-log redaction includes string values anywhere below a sensitive key in the child config (so also `env` entries with sensitive names), and the actual values of `inheritEnv` variables whose names are sensitive. Key matching (`is_sensitive_key` in `config.py`) includes `api_key`/`api-key`, keys ending in `_key`/`-key` (e.g. `ai_context_key`), tokens, secrets, passwords, authorization, email, and credentials. Ordinary values such as `PATH`, `TEMP`, `USERPROFILE`, or a child's `PYTHONPATH` are not treated as secrets, so error text that mentions them stays readable.
- Redaction applies to Hub audit fields `error` and `error_summary`; it does not rewrite successful tool results or child error results forwarded to the agent. Child MCPs must not include credentials in their errors or logs.
- Do not give child MCPs broad access to the Hub's private config directory.
- Child tool output is untrusted. The Hub forwards successful results to the Agent unchanged, so the Agent must treat them as data ([client-rules.md](client-rules.md)). Error text is redacted before it is written to logs. If a call raises (child crash, timeout), the Agent gets only `Child MCP call failed. See Hub log request_id=<n>`. If the child returns an error result, that result reaches the Agent unchanged, and its redacted first line is logged as `error_summary`.

## Audit events

The Hub writes JSON Lines records for startup/shutdown, config validation, child connection, tool discovery, and every child or `hub__status` call. Job tools (`hub__submit_issue`, `hub__approve_job`, …) record their state changes in the job store timeline instead. Fields: `timestamp`, `event`, and when relevant `request_id`, `feature`, `server`, `tool`, `duration_ms`, `error_type`, and redacted `error` / `error_summary`. The full list is in the event reference below.

`hub__status` reports only child IDs, counts, and states; it does not expose config paths or credentials. Its calls use the same `tool.call.started` and `tool.call.succeeded` audit events as child tools.

On Windows, `chmod` does not restrict files, and a repo on a secondary (non-system) drive usually inherits `Authenticated Users` **modify** access. The logon task runs `scripts/service_launcher.pyw` and the `.venv` from this checkout as you, so any account that can edit it could run code as you. `hubctl install` therefore:

- restricts the whole checkout to the current user, SYSTEM, and Administrators when another account can write to it (`Protect-HubCheckout`, verified with `Test-OthersCanWrite` after the change), and
- limits `config/private/` further to the current user and SYSTEM.

It does not change the parent folder or the child repositories, which belong to other projects. When the parent folder is writable by other accounts it prints a warning with the `icacls` command to restrict it or each child repository; the Hub launches those children as you as well. Any process running as the same user can read the bearer token and act as the Hub; that is the trust boundary.

The shared HTTP endpoint binds only to `127.0.0.1`, checks `Host` and `Origin`, and requires the local bearer token. Only HTTP and lifespan scopes pass the middleware; a websocket is closed (code 1008) before it reaches the app. `/health` returns only liveness. Every `/dashboard` response carries `Cache-Control: no-store`, so approval codes and job data are not kept in a browser or proxy cache.

**Write approval is a human step.** The three routes that reveal or use an approval (`/dashboard/api/jobs/{id}/pending-call`, `/approval-code`, `/approve`) accept only the paired browser session and answer `403` to the bearer token. Every MCP client holds that token, so a prompt-injected agent with shell access could otherwise read the code and approve its own write. All other dashboard routes still accept the token, which `hubctl` uses for status, pairing, and shutdown. Dashboard pairing tokens are single-use and short-lived; browser sessions are `HttpOnly`, same-site cookies. The live activity SSE endpoint uses that same paired session and emits only call lifecycle metadata, never tool arguments, results, or error text. HTTP access logs are disabled. The pending-write preview masks only secret-like values before they reach the browser (see below). Pending arguments are encrypted at rest and never included in metadata events.

Investigation specialists are offered only their role's tools that the child marks `readOnlyHint=true`, plus the Hub's encrypted cache-refresh allowlist (`AUTOMATIC_ENCRYPTED_CACHE_WRITES` in `jobs/policy.py`: `jira__sync_ticket`, `pr__sync_my_open_prs`, `pr__get_pr_context_pack`). Every other write or export tool is withheld; if a model names one anyway, the call is rejected and the model receives an error tool message. An allowlisted refresh runs automatically except `jira__sync_ticket` with `force=true`, which needs approval even when the child marks the tool read-only.

Before such a gated write runs, the Hub stores the exact tool and arguments, then waits in `awaiting_approval`. The dashboard shows the tool and the full arguments as pretty-printed JSON in a scrollable block, so the user sees what will run. Only secret-looking values are masked: values under argument names containing token, secret, password, passwd, pwd, passphrase, authorization, bearer, API/access/private key, credential, cookie, or session ID, plus bearer/basic credentials, `key=value` secrets, known token formats, private keys, URL credentials, and the configured secrets inside any string. Emails, issue keys, summaries, and other ordinary fields are shown unchanged. A preview longer than 64 KB (`MAX_PREVIEW_CHARS`) is cut there and marked `truncated`, with the total length. A user can approve there (after a confirmation prompt), or copy a single-use code into an AI client. Each write in a job waits for its own approval: a second write in the same job gets a new pending call, code, and wait; a job has one pending write at a time. Approval codes last 10 minutes; the pending write is bounded by the 15-minute job window. Direct child calls still follow each child's existing contract and annotations.

**Restart.** One-time MCP approval codes live only in the Hub process, so they do not survive a restart; dashboard approval still works. At start, a job still `awaiting_approval` whose 15-minute window (counted from job creation) has passed becomes `partial` with event `approval.expired`, and its pending write is deleted unrun. A younger one stays approvable from the dashboard until its window ends, then is settled the same way. Approving it runs only the stored operation, once.

Investigation timeline events live in the SQLite job store, not in `logs/mcp-hub.jsonl`. They contain state changes, agent/child tool names, timing, and evidence IDs only. The dashboard's 30-day activity table stores one row per child tool call with child ID, tool name, MCP client label, investigation role when applicable, start/end time, duration, state, and error type. It does not store prompts, arguments, results, or error text. Requests, tool arguments, full tool results, and summaries are stored in protected blobs. Job rows, activity rows, and their timelines are deleted at the 30-day expiry.

Do not log credentials, full environment maps, complete tool arguments, or complete tool results by default. Log only safe metadata or explicitly reviewed, redacted fields. Keep logs local under `logs/`, restrict access, and rotate them to avoid unbounded growth.

`scripts/check_leaks.py` scans tracked and non-ignored untracked files, including `uv.lock`, for machine paths, non-`example.com` email domains, and `config/private/`, `.local/`, `logs/`, `.env`, or `.env.*` path components. It skips only its own source and reports locations without printing matched text.

When the Hub uses MCP `stdio`, stdout is reserved for protocol messages. Send application logs to a file or stderr; never write diagnostic text to stdout.

## Diagnostic files

- **Hub audit log:** `logs/mcp-hub.jsonl`, redacted JSON Lines, rotated at 5 MB with 3 backups (`.1`–`.3`). Each `AuditLog` writes to the file it is given; all writers of one file in a process share one rotating handler. Agents report failures from this file.
- **Test and tool overrides:** `MCP_HUB_LOG_DIR` moves the audit log and `children/` logs, and `MCP_HUB_PRIVATE_DIR` moves every private file (`hub.json`, service token, job store, launch file). They apply to the Python processes only (`hubctl.ps1` always uses the checkout paths). The stdio tests set both for the Hub processes they start, and in-process tests pass temporary paths, so the test suite never writes to the checkout's `logs/` or `config/private/`. Leave them unset in normal use.
- **Child stderr:** `logs/children/<server-id>.log`. Each child's stderr is a pipe that a Hub background thread appends to this file, so neither the child nor the event loop waits on disk. Before a write would take the file past 5 MB it is renamed to `<server-id>.log.1` (replacing the previous one); a file already over 5 MB is rotated when it is first opened. The text is the child's own output and is **not redacted**: it may contain the child's secrets. The user inspects these files; agents never open or print them (see [AGENTS.md](../AGENTS.md#private-files)).
- **Service stderr:** `logs/mcp-hub-service.err`. `scripts/service_launcher.pyw` sends the shared service's stderr (Python tracebacks, MCP SDK warnings such as an unparsable child line) here and its stdout to the null device. A file over 5 MB is rotated to `mcp-hub-service.err.1` when the service starts. Like child stderr, it is not redacted.
- The scheduled task restarts a failed service up to 3 times, one minute apart. The setting is registered by `hubctl install`; run it again to apply it to an existing task.

## Event reference (`logs/mcp-hub.jsonl`)

| Event | Level | Meaning |
|-------|-------|---------|
| `config.load.failed` | ERROR | Hub config or a child config is invalid; `error` names the problem. The Hub does not start. |
| `server.connect.failed` | ERROR | On the first attempt, the child process didn't start or finish the MCP handshake within `connectTimeoutSeconds` (`error_type=TimeoutError` when it timed out). Its tools are skipped and it is retried in the background. |
| `server.list_tools.failed` | ERROR | On the first attempt, the child started but tool listing failed or timed out. Its tools are skipped and it is retried in the background. |
| `server.connected` | INFO | Child is up after its first attempt; `tools_count` is how many tools it offers. |
| `server.disconnected` | WARNING | A connected child's session ended; `error_type` is the cause (`ConnectionClosed` when the child exited or closed stdout). Calls return `ChildUnavailable` until it reconnects. |
| `server.reconnected` | INFO | A child connected again after a failed attempt or a lost session; `tools_count` as above. Its routes are rebuilt. |
| `server.reconnect.failed` | WARNING | A retry failed; `error_type`/`error` name the cause and `retry_in_seconds` is the delay before the next attempt (1 s doubling to 60 s). |
| `route.skipped` | WARNING | An allowlisted tool wasn't exposed; `reason` is `server unavailable` or `tool not found`. Written at start, and for a reconnected child's tools it no longer lists. |
| `hub.tools.registered` | INFO | Final list of exposed tool names; written again when a reconnect changes the list. |
| `hub.started` / `hub.stopped` | INFO | Lifecycle. |
| `hub.tools.listed` | INFO | A client listed tools. |
| `tool.call.started` | INFO | Call routed; it carries `request_id`, `feature`, `server`, and `tool`. |
| `tool.call.succeeded` | INFO | Call finished; includes `duration_ms`. |
| `tool.call.failed` | ERROR | Exception, timeout (`error_type=TimeoutError`), the child has no live session (`error_type=ChildUnavailable`; answered immediately), or a child error result (`error_summary`, redacted). An exception group is logged by its first leaf exception. |
| `tool.call.denied` | WARNING | A client called a tool that isn't allowlisted. |
| `tool.call.cached` | INFO | A read-only call with a `cacheSeconds` TTL was answered from the Hub result cache (or joined an identical call in flight); the child was not called. Includes `duration_ms`. |
| `tool.call.interrupted` | WARNING | The call was cancelled before the child answered. |
| `preflight.check` | INFO / WARNING | A pre-start check of one child: `server`, `step` (runtime name or `prepare`), and `ok`. No child output. |
| `extension.unknown` | WARNING | The launch file named an extension the Hub does not know; it is ignored. |
| `launch.file.invalid` | WARNING | `config/private/launch.json` could not be read, decoded, parsed, or deleted (`error_type`). The Hub starts in fast mode. |
| `extension.observer.failed` | ERROR | An extension observer raised; only `error_type` is logged and the tool call continues. |
| `activity.persist.failed` / `activity.persist.dropped` | ERROR / WARNING | The activity UI could not write its batched call records, or dropped records because its queue was full. Only shown with `-Ui`. |

In the job timeline (SQLite, not this log), `tool.call.reused` marks an identical call that an investigation job answered from its earlier result and evidence ID; `agent.timed_out` marks a specialist stopped at the job time limit, and `approval.expired` a pending write whose window ended. `hub.started` also lists the loaded `extensions` (empty in the default fast mode). The result cache lives only in the Hub process memory and is cleared on restart; it never writes tool results to disk. It stores only results that pass the Hub's failure check: not `is_error`, and a first text block that does not start with `❌` or `Error:`. A successful or failed call to a tool without `readOnlyHint=true` drops that child's cached results.

To diagnose a failure, search for the `request_id` returned in the tool's error text.

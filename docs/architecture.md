# Architecture

## Responsibilities

- **Hub config** defaults to the four curated child MCPs and their tool allowlist. Known sibling repos are discovered at startup; private config overrides defaults, including explicit disables.
- **Per-MCP private config** holds local settings or references to credentials needed by one child MCP.
- **Hub runtime** validates config, connects to child MCPs, exposes allowlisted tools, routes calls, and writes audit events.
- **Shared service** keeps one Hub runtime and one connection per child alive for the signed-in Windows user. It exposes authenticated Streamable HTTP and retains the legacy stdio endpoint; a thin stdio bridge lets clients share the service.
- **Issue jobs** coordinate bounded logs, source, Jira, and PR specialists. Job details are encrypted with Windows DPAPI in SQLite and retained for 30 days.
- **Dashboard** reports child and agent readiness, lists shared jobs, shows redacted pending-write previews, and controls the per-user scheduled task.
- **Child MCP** owns its domain logic, credentials, and upstream service behavior.
- **Agent docs** explain tool purpose and operational rules; they do not enforce access control. The Hub must enforce the configured allowlist.

## Module layout

Dependencies flow one way; a module uses only the public functions and methods of the modules before it.

| Layer | Modules (`src/mcp_hub/`) | Owns |
|---|---|---|
| Configuration | `config.py`, `service_config.py` | Loading and validating the Hub manifest, child configs, `prepare` specs, and the private service settings |
| Contracts | `audit.py`, `extensions.py` | The redacted audit log; the extension contract (`CallObserver`, `ActivitySource`, `DashboardExtension`) and the start-time launch flags |
| Hub runtime | `server.py` | Child connections, the tool allowlist and routing, prompts, and the MCP server factory |
| Transport | `http.py`, `bridge.py` | Authenticated Streamable HTTP, and the stdio bridge with its optional presence stream |
| Service | `service.py`, `preflight.py` | The shared HTTP service lifecycle and extension installation, and the pre-start runtime and dependency checks |
| Features | `dashboard/`, `jobs/`, `agents/`, `security/` | Dashboard API and UI, investigation jobs, specialist agents, and DPAPI encryption |
| Extension | `activity_ui/` (`tracker`, `client_sessions`, `middleware`, `recorder`, `routes`, `extension`) | Optional live activity, connected-client tracking, the batched `tool_calls` writer, and the activity dashboard routes |

The dashboard UI is plain ES modules served from an allowlist in `dashboard/api.py`; its text comes from `dashboard/locales/<lang>.json` (English default, Vietnamese optional). `app.js` loads the activity modules (`barn.js`, `activity.js`, `stream.js`) through `ui_extension.js` with a dynamic `import()` only when `/dashboard/api/status` reports `extensions.activityUi`. Code standards for all modules are in [AGENTS.md](../AGENTS.md#code-standards).

## Core and extensions

The core tool-call path is `HubRuntime.call_child_tool`: route lookup, one audit line, the child call under its timeout (`_invoke_child`, the single point a result cache wraps), and one audit line for the outcome. `HubRuntime.observers` is empty by default, so the core path has no activity tracking, client tracking, or store access. Core modules (`server`, `http`, `extensions`, `dashboard/api.py`, `jobs`) never import an extension.

An extension implements `CallObserver` (`call_started`, `call_finished`, `client_seen`; metadata only, never arguments or results) and may contribute a `DashboardExtension` (routes, status fields, and the `hubctl` flags that keep it on across a dashboard restart). Observer hooks run on the event loop, so they only update memory or enqueue; a failing observer is logged (`extension.observer.failed`) and never fails the call.

Start-time flow: `hubctl start|restart|open|install -Ui` writes `config/private/launch.json` (`{"extensions": ["activity-ui"]}`) right before starting the task, and removes it without `-Ui`. `service.py` reads and deletes the file at start (`extensions.read_launch_extensions`) and, only when `activity-ui` is requested, imports `activity_ui` and calls `install(runtime, store=..., audit=...)`. That adds the observer, the `subscriptions/listen` presence handler, `ClientSessionMiddleware` inside the bearer check, the `/dashboard/api/activity` and `/dashboard/api/activity/stream` routes, and the `activity`/`clients` status fields. `/dashboard/api/status` always reports `extensions.activityUi`. With the UI on, finished calls are queued in memory and written by a background task in batches (up to 50 rows or 1 second) with one multi-row INSERT via `asyncio.to_thread`; the queue is flushed after the job runner closes at shutdown.

`scripts/bench_call_path.py` measures sequential tool-call latency through the HTTP app with the activity UI off and on.

## Request path

Direct call: `AI client → authenticated shared Hub → child MCP client → child MCP tool → metadata-only Hub audit log`

Investigation call: `AI client → job queue → fixed specialist roles → allowlisted child MCP tools → encrypted job result → coordinator summary`

Every exposed tool should map to exactly one child server and tool name. Use namespaced exposed names such as `serverId__toolName` to avoid collisions. A feature may expose tools from multiple child MCPs. Keep the initial design as a curated proxy; add cross-server composite tools only when a real workflow needs them.

## Failure handling and child selection

- A child that fails to start, or a configured tool the child does not list, is logged (`server.connect.failed`, `route.skipped`) and skipped; the Hub starts with the remaining tools. With no available child tools, it still exposes `hub__status`.
- `hub__status` reports detected sibling count, connected count, and each known child's state. A child with unfilled `<SET_BY_USER>` placeholders in its private config is marked `credentials required`; its tools are not exposed. The built-in templates use empty values instead, so a child without Hub credentials still starts and uses its own local files (see [Configuration sources](child-mcp-compatibility.md#configuration-sources)). Startup failures are marked `unavailable`; detailed diagnostics remain in the Hub log.
- Each call is limited by the child's `timeoutSeconds` (default 60); a timeout is logged as `tool.call.failed` with `error_type=TimeoutError`.
- The Hub exposes one MCP prompt per connected child, named after its server ID. Clients that support prompts show it as a slash command (the exact format is client-defined); it tells the Agent which Hub tools belong to that child and includes `docs/mcps/<server-id>.md`.
- On connect, the Hub sends server instructions, essentials first: a summary under 512 characters (`INSTRUCTIONS_SUMMARY` in `server.py`), the tool list per child, and then [client-rules.md](client-rules.md), read at startup. Clients may truncate them (Claude Code cuts at 4,096 characters), so only the tail of the rules can be lost. Clients that ignore server instructions get the same rules from the always-on rule installed by [connecting-a-client.md](connecting-a-client.md) step 7.

## Local child servers

Children run over `stdio`. The Hub launches each one with an argument array (never a shell string) in the child's `cwd`, passing only the MCP SDK's default safe variables, the `inheritEnv` names, and the child's `env` values. Child repositories are expected to sit next to this repo, so configs reference them as `../<repo>`. The Hub resolves relative paths in memory; tracked config never contains machine paths. A private Hub manifest can override each known child or feature, and `enabled: false` takes precedence over discovery. The source reader is a separate child repo; it reads only project roots registered by alias in its own private configuration. The Hub separately validates those aliases and environments for job requests. An explicit `MCP_HUB_CONFIG` retains the private-manifest-only behavior.

## Shared Windows service

`scripts/hubctl.ps1 install` prepares a per-user Task Scheduler task named `mcp-hub` with limited privileges and starts `mcp-hub-service` through `pythonw.exe` without a console window, independently of terminal windows and battery power. Before every start, `hubctl` syncs the locked environment and runs `python -m mcp_hub.preflight --install`, which checks each enabled child's declared runtime (installing or upgrading it with winget when needed) and runs the child's own dependency step. The service binds only to loopback (`127.0.0.1:8765`), requires a bearer token for MCP, and keeps the original per-client stdio launcher. HTTP-capable clients can use `scripts/http_headers.ps1`; other clients use `scripts/start.ps1 -Bridge`, which forwards stdio MCP messages to the shared service. The authenticated dashboard shows jobs and child state; with the activity UI (`-Ui`) it also visualizes live child MCP tool activity. Setup and migration are in [the shared-service runbook](operations/shared-agent-hub.md).

The job runner allows two active jobs, a 15-minute lifetime, and 30 child-tool calls per job. Jobs missing an environment wait for validated input. On restart, active investigation state is marked interrupted and never replayed. A pending write can remain awaiting approval; once approved, the exact stored operation is executed once and the job reports partial because its model investigation is not replayed. Dashboard approval uses a session cookie; the MCP alternative requires a one-time code visible in the dashboard.

Each child MCP has a contract at `docs/mcps/<server-id>.md`; see [mcps/README.md](mcps/README.md). To add one, follow [adding-a-child-mcp.md](adding-a-child-mcp.md).

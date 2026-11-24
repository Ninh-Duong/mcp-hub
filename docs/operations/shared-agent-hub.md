# Shared Agent Hub operations

This is the operator process for one Windows user running a single Hub for several local AI clients. The shared service, investigation jobs (DPAPI), and the winget runtime install are Windows-only; see the [support matrix](../packaging-and-qc.md#support-matrix). The source reader used by investigations is the sibling repository `mcp-read-source`.

## 1. Prepare the repositories

Keep the Hub and child repositories as siblings:

```text
<parent>/
  mcp-hub/
  mcp-server-logs/
  mcp-jira-sync/
  mcp-pr-companion/
  mcp-read-source/
```

Use [the README preparation steps](../manual-setup.md#default-setup) for the existing logs/Jira/PR children. The source child requires Python 3.12 and uses the Hub's locked Python environment when launched by the Hub. Prepare its standalone environment with `uv sync --locked` only if using it outside the Hub.

## 2. Configure projects and agents

Copy examples into ignored private files, then edit locally. Never paste keys or local roots into chat.

```powershell
New-Item -ItemType Directory -Force config/private, config/private/mcps, ../mcp-read-source/config/private | Out-Null
Copy-Item config/agents.example.json config/private/agents.json
Copy-Item config/projects.example.json config/private/projects.json
Copy-Item ../mcp-read-source/config/projects.example.json ../mcp-read-source/config/private/projects.json
```

- In the Hub's `config/private/agents.json`, set a provider (`openai`, `gemini`, or `anthropic`), model, and API key independently for `coordinator`, `logs`, `source`, `jira`, and `pr`. For investigation jobs, pairing a strong reasoning model on `coordinator` with fast, lower-cost models on the specialists (`logs`, `source`, `jira`, `pr`) keeps triage thorough and responsive. The key is read only inside the Hub process. Check readiness with `uv run python scripts/private_config.py agent-status`.
- Optionally copy `config/client_profiles.example.json` to `config/private/client_profiles.json` to customize per-client execution modes and model choices for direct `hub__plan` sessions ([usage-policy.md](../usage-policy.md#session-execution-confirmation)). Verify the active profiles with `uv run python scripts/private_config.py client-status`.
- In the Hub's `config/private/projects.json`, register each project alias, optional `default_env`, allowed environments (`dev`, `stg`, `prod`), and optional log targets per environment (below). Run `uv run python scripts/private_config.py project-status` to validate the aliases and environments.
- In the source child's `config/private/projects.json`, map the same alias to a local repository root. The source child reads the root mapping; the Hub's project config contains only the alias, environment choices, and log targets.
- Copy and fill the private MCP configs for logs/Jira/PR as described in their contracts under `docs/mcps/`. Their existing local fallbacks still work.

The examples start with empty `projects` maps so a copied file is valid before the user adds a repo. A matching entry pair looks like this:

Hub `config/private/projects.json`:

```json
{"version":1,"projects":{"app":{"default_env":"dev","allowed_envs":["dev","stg","prod"],
  "logs":{"dev":{"organization":"<SET_BY_USER>","stream":"<SET_BY_USER>","rcid_field":"rcid"}},
  "sql":{"dev":{"default_database":"<SET_BY_USER>","databases":["<SET_BY_USER>"]}}}}}
```

`logs` is optional. Per allowed environment it names the OpenObserve `organization` and `stream` where the project's logs live, and the request-ID field (`rcid_field`, default `rcid`). The log playbooks (`rcid-bug`, `service-errors`, `jira-ticket-bug`) need it: `hub__plan` returns these targets to the agent, so the agent never has to discover them, and refuses a log playbook for an environment without them ([usage-policy.md](../usage-policy.md#playbooks)). Optional `sql` entries give `default_database` and `databases` hints through `hub__plan` and `hub__list_projects`. `project-status` lists which environments have `logs` and `sql` entries, never the values.

Source child `config/private/projects.json`:

```json
{"version":1,"projects":{"app":"<LOCAL_REPOSITORY_ROOT>"}}
```

Start/restart the Hub after configuration changes. On startup the entire configuration is validated; a failure appears in `config.load.failed` in the Hub log. Do not edit the generated `config/private/service.json` by hand.

## 3. Install and operate the service

Run from the Hub repository in a normal, non-administrator PowerShell session:

```powershell
.\scripts\hubctl.ps1 install
.\scripts\hubctl.ps1 status
```

`install` registers a per-user Task Scheduler job at logon and starts the Hub. A Hub that is already running is stopped first, because the running service locks `.venv\Scripts\mcp-hub-service.exe` and the environment sync may need to replace it. If another account can write to the checkout, it first restricts the checkout to you, SYSTEM, and Administrators, and it warns when the parent folder holding the child repositories is writable by others (see [security-and-logging.md](../security-and-logging.md)). The server listens only on `127.0.0.1:8765`. Use these commands for recovery and change activation:

```powershell
.\scripts\hubctl.ps1 status
.\scripts\hubctl.ps1 start
.\scripts\hubctl.ps1 restart
.\scripts\hubctl.ps1 stop
```

The per-user scheduled task `mcp-hub` runs the prepared environment through `pythonw.exe`, which starts the `mcp-hub-service.exe` process without a console. The Hub opens no window and stays in the background when a PowerShell or Windows Terminal window closes. The task is allowed to start and continue on battery. Run `hubctl install` again after changing the Python version or task setup.

The desktop status app is a separate product with its own installer and its own sign-in entry (see [desktop-app.md](../desktop-app.md)); `hubctl app` opens the installed app, or the source copy when none is installed, and then says where to download the installer; `hubctl app -Install` downloads the latest release, checks its SHA-256, and runs its setup. Routine updates are available in the app; `hubctl update` is the background coordinator it uses to check, update, or retry enabled child repositories and the Hub. `install` removes the `mcp-hub-status` logon task that older versions registered for it. To stop the Hub starting at logon, use Settings → Hub in the app, or `hubctl autostart -Target hub -State off` (`hubctl autostart` prints the state). This disables only the task's logon trigger, so `hubctl start` keeps working, and `hubctl install` keeps the choice. A copy of the app run from source keeps Qt libraries in `.venv` open, so `start`, `restart`, and `install` close it before syncing the environment and reopen it once the Hub is healthy; the installed app is never stopped. When an AI client's bridge keeps `.venv\Scripts\mcp-hub-bridge.exe` open and the sync cannot replace it, the Hub still starts with the current environment and `hubctl` warns; close the AI clients and run `hubctl restart` to finish the sync.

### Pre-start checks

`install`, `start`, and `restart` run the same preparation before starting the task, so one command is enough on a fresh machine:

1. Bootstrap uv and Python into the repository if they are missing, then sync the locked Hub environment (`uv sync --locked`).
2. Run `python -m mcp_hub.preflight --install`. For each enabled child with a `prepare` block in `config/hub.example.json`, it checks the declared runtime and version (for example Node.js 22+ for `mcp-server-logs`, 20.12+ for `mcp-pr-companion`). A missing or outdated runtime is installed or upgraded with winget; Windows may show a UAC prompt. It then runs the child's own dependency step (`node start.mjs --preflight`, `node bin/cli.js prepare`) in the child repository.
3. Start the task, wait for `/health`, and print the status.

A child that still fails is reported (`preflight.check` in the Hub log) and the Hub starts with the remaining children. Child output is not printed; run the reported command in the child repository for details. `hubctl preflight` checks the runtimes only, without installing or starting anything, and exits non-zero when a requirement is not met:

```powershell
.\scripts\hubctl.ps1 preflight
```

The Hub service merges new `PATH` entries from the registry when it starts, so a runtime installed by winget is found without signing out.

The Hub serves no browser interface. Use the desktop Status app for live service, child, AI client, settings, and update state. The local HTTP API remains available to that app; its control actions use the same authenticated shutdown flow. The Hub gives active HTTP requests up to 20 seconds to drain, then cancels them and closes its job runner and child MCP connections before the scheduled task exits. `hubctl` waits up to 45 seconds for a clean stop. If the task is running but `/health` is unavailable, `hubctl stop` reports the condition rather than force-killing the task.

### Fast mode and activity tracking

By default the Hub starts in **fast mode**: a tool call goes route, audit log, child, with no activity tracking, no client tracking, and no database write.

Activity tracking is an extension chosen at start. `hubctl start -Ui`, `restart -Ui`, and `install -Ui` write `config/private/launch.json` (`{"extensions": ["activity-ui"]}`, no secrets) just before starting the task; the service reads and deletes it at start. The next start without `-Ui`, including the logon-triggered one, uses the saved mode again; without `-Ui` the commands also remove a leftover file.

To keep activity tracking on for every start, turn on **Track AI clients** in the desktop app (Settings → Hub). It saves `"extensions": ["activity-ui"]` at the top level of `config/private/hub.json`, and the service starts the union of that list and `launch.json`. Turning it off removes the key. Either change applies at the next start; the app offers a restart. `hubctl status` prints the current mode.

With activity tracking on, the desktop app shows connected clients and child usage. The tracking extension stores only metadata retained for 30 days; it does not include tool arguments or results. The desktop app receives live events over Server-Sent Events. A background batch writer stores each finished call as one row (up to 50 rows or 1 second per write), so the call path never touches the database. Calls still running when the Hub stops are stored as interrupted.

### Connected AI clients

This panel, the desktop app's AI clients card, and the `Clients:` line of `hubctl status` need the activity UI (Track AI clients, or `-Ui`); in fast mode the Hub does not track clients.

The desktop app's **Connected AI clients** card and `hubctl status` (`Clients:` line) show which AI clients use the shared Hub. MCP clients are stateless, so the Hub uses three signals, grouped by client name:

- **Live, stdio bridge:** each `start.ps1 -Bridge` process holds a `subscriptions/listen` stream open while it runs and reopens it within 5 seconds after a Hub restart. The client appears on connect and leaves when its bridge exits. A Hub in fast mode does not serve this stream; the bridge then stops holding it until the bridge restarts. The bridge sets no read timeout of its own, so a child call can run for the child's full `timeoutSeconds`.
- **Live, 2025-era HTTP session:** listed until the client deletes the session or it has been idle for 30 minutes.
- **Recently active:** any other authenticated request, such as a stateless direct-HTTP client, lists its client for 30 minutes after the last request. A closed bridge or session also stays in this state for 30 minutes.

Only client names and times are kept, in memory. Session IDs and tokens are never shown. A client configured with the legacy per-client launcher (`start.ps1` without `-Bridge`) runs its own Hub, so the shared service cannot see it.

### Slow children and tool definition changes

The service starts serving after `children.startupWaitSeconds` (default 10) even if a child has not answered; that child's tools appear when it connects, and clients are told about the new tool list.

The Hub pins each child tool definition in `config/private/tool_pins.json` (hashes only). When a definition changes, the Hub writes `tool.definition.changed` to the audit log and lists the tool under `tool_pins` in `hub__status` while it differs; the tool keeps running. To accept a change, stop the service, delete that tool's entry from the file, and start again: the next start pins the new definition. Hidden Unicode or an over-long description writes `tool.description.suspicious`. A corrupt or unreadable pin file is treated as empty, so every tool is pinned again at the next start without a warning.

## 4. Connect each AI client

Each client still has exactly one entry named `mcp-hub`:

- Prefer authenticated Streamable HTTP for clients that can run a local dynamic-header helper. The endpoint is `http://127.0.0.1:8765/mcp`; the helper is `scripts/http_headers.ps1`. Current Codex and Claude Code guides include this configuration.
- Use the stdio bridge for clients that cannot safely supply the bearer header. It starts with `scripts/start.ps1 -Bridge -ClientName "<AI_CLIENT>"` and forwards MCP protocol messages to the shared Hub. It reads the bearer token in process and labels calls in activity tracking. HTTP clients are labeled from MCP `clientInfo`.
- The original `scripts/start.ps1` without `-Bridge` still launches an isolated stdio Hub for development or a temporary compatibility fallback. It does not share jobs with the service.

Follow [connecting-a-client.md](../connecting-a-client.md) and that client's guide. The setup agent must ask before editing a user-level client config and before removing an old direct child entry. Replace only child server entries that point to the logs/Jira/PR repos, then install/refresh the Hub rule and `/mcp-hub` command. This repository change does not modify any AI client settings.

## 5. Run and inspect an issue job

Start with `hub__list_projects`, which returns aliases and allowed environments but no local roots. `hub__submit_issue(project, question, env?, issue_key?, rcid?)` returns a `job_id` immediately, or an error when 20 jobs are already queued (try again after one starts or is cancelled). If both `env` and the project's default are absent, the job waits in `awaiting_input`; provide a valid environment with `hub__provide_job_input`. The coordinator dispatches only the fixed specialist roles. Each specialist sees only its role's read-only child tools and the encrypted cache refreshes the Hub allows (`jira__sync_ticket`, `pr__sync_my_open_prs`, `pr__get_pr_context_pack`); export and other write tools are not offered. Direct child tools remain available beside the job tools.

Inspect the job from any connected client with `hub__get_job(job_id)`. State and evidence events are shared because all clients use the same Hub process and per-user SQLite database. `hub__get_job_detail` loads bounded pages of the encrypted full tool output only when requested.

Limits are two active jobs, 20 queued jobs, a 15-minute investigation window per job, and 30 child-tool calls per job. A job waiting for approval does not hold an active slot. A failed tool call is reported to the specialist, which continues. At the 15-minute limit, unfinished specialists stop, the coordinator gets up to 90 seconds to summarize the evidence already collected, and the job ends `partial`. Unconfigured providers or children produce a partial result; the Hub does not guess a missing environment, alias, or source.

## 6. Approve a pending write

A gated write (today: a forced `jira__sync_ticket` with `force=true`) waits in `awaiting_approval`. The browser job review and approval interface has been removed. The Hub does not execute a gated write unless a user supplies a valid one-time code through `hub__approve_job(job_id, code)`; each write waits for its own approval, and a code expires after 10 minutes and works once. Since the desktop app does not expose job review or approval, a gated write without a separately delivered code remains pending and expires under the job window. The user must explicitly choose to approve; an agent saying “approved” is not enough.

Documented encrypted cache updates made automatically as part of read workflows do not prompt. Direct child calls keep their current behavior and must follow their child contract. If the service restarts while an investigation is running, the job is marked interrupted and model work is not replayed. One-time codes do not survive a restart. A job still waiting for approval remains pending until 15 minutes after it was created, then becomes `partial` with event `approval.expired` (at start for older ones) and its write is not run. Approving it in time runs only its exact saved operation once and returns a partial job because the broader investigation does not resume.

## 7. Retention and support

SQLite job records, events, pending actions, summaries, tool results, and daily child-call history are removed after 30 days. Sensitive blobs are protected at rest with Windows user-scope DPAPI. The database belongs to the Windows account that created it and cannot be decrypted under another account. Hub audit logs remain metadata-only and are in `logs/mcp-hub.jsonl`.

| Symptom | Check |
|---|---|
| Desktop update reports a failed component | In mcp-hub Status, read the component status and choose **Retry**. A repository with local edits or diverged history is skipped safely and needs its local Git state resolved before retry. |
| Service task runs but health is unavailable | `hubctl status`; inspect the redacted `config.load.failed` or child connection event in `logs/mcp-hub.jsonl`; a crash traceback is in `logs/mcp-hub-service.err`; run `uv run python scripts/smoke.py` |
| A child shows `reconnecting` or calls return `ChildUnavailable` | The child failed its last attempt or its session ended; the Hub retries it every 1 s up to 60 s. Find `server.disconnected`, `server.connect.failed`, or `server.reconnect.failed` for that `server` in `logs/mcp-hub.jsonl` (`error_type`, `retry_in_seconds`); the user can read the child's own output in `logs/children/<server-id>.log` (agents must not print it). A child that times out on first start (`error_type=TimeoutError`) may need a larger `connectTimeoutSeconds`. |
| A child keeps failing with `Connection closed` right after a `git pull` | A new dependency may not be installed yet. The sign-in task and a crash restart sync the environment by themselves when `uv.lock` changed; to do it now run `hubctl restart`. If `logs/mcp-hub-service.err` says `the environment sync did not finish`, close the AI clients (they hold files in `.venv`) and run `hubctl restart` again. |
| A child stays `connecting` at start | Its first attempt is still running; startup waits at most its `connectTimeoutSeconds` (default 120). |
| HTTP client gets 401 | Restart after installing the service so `service.json` exists; reconnect so the client header helper runs again |
| Source alias is unknown | Check `project-status`, then check the matching alias in the source child's private project config and restart |
| Agent shows unconfigured | Check `agent-status`; edit the provider/model/key in private `agents.json`, then restart |
| Agent shows "Provider package not installed" | The provider's SDK (`openai`, `anthropic`, or `google-genai`) is missing from the Hub environment; run `hubctl start` to resync the locked environment |
| Agent shows "Needs Windows DPAPI" | The Hub runs off Windows, where job details cannot be encrypted; `hub__submit_issue` refuses jobs there. Direct child tools still work |
| Write waits unexpectedly | Inspect the job state with `hub__get_job`; a forced Jira re-sync is gated by design, and other writes are never offered to specialists |
| Submitting a job says the queue is full | 20 jobs are already queued; wait for one to start or cancel one with `hub__cancel_job` |

Do not share private configuration files, job databases, or full tool results in issue reports. Report request IDs and redacted error metadata only.

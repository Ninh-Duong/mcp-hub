# Shared Agent Hub operations

This is the operator process for one Windows user running a single Hub for several local AI clients. The shared service, its dashboard controls, investigation jobs (DPAPI), and the winget runtime install are Windows-only; see the [support matrix](../packaging-and-qc.md#support-matrix). The source reader used by investigations is the sibling repository `mcp-read-source`.

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

Use [the README preparation steps](../../README.md#default-setup) for the existing logs/Jira/PR children. The source child requires Python 3.12 and uses the Hub's locked Python environment when launched by the Hub. Prepare its standalone environment with `uv sync --locked` only if using it outside the Hub.

## 2. Configure projects and agents

Copy examples into ignored private files, then edit locally. Never paste keys or local roots into chat.

```powershell
New-Item -ItemType Directory -Force config/private, config/private/mcps, ../mcp-read-source/config/private | Out-Null
Copy-Item config/agents.example.json config/private/agents.json
Copy-Item config/projects.example.json config/private/projects.json
Copy-Item ../mcp-read-source/config/projects.example.json ../mcp-read-source/config/private/projects.json
```

- In the Hub's `config/private/agents.json`, set a provider (`openai`, `gemini`, or `anthropic`), model, and API key independently for `coordinator`, `logs`, `source`, `jira`, and `pr`. The key is read only inside the Hub process. Check readiness with `uv run python scripts/private_config.py agent-status`.
- In the Hub's `config/private/projects.json`, register each project alias, optional `default_env`, and allowed environments (`dev`, `stg`, `prod`). Run `uv run python scripts/private_config.py project-status` to validate the aliases and environments.
- In the source child's `config/private/projects.json`, map the same alias to a local repository root. The source child reads the root mapping; the Hub's project config contains only the alias and environment choices.
- Copy and fill the private MCP configs for logs/Jira/PR as described in their contracts under `docs/mcps/`. Their existing local fallbacks still work.

The examples start with empty `projects` maps so a copied file is valid before the user adds a repo. A matching entry pair looks like this:

Hub `config/private/projects.json`:

```json
{"version":1,"projects":{"app":{"default_env":"dev","allowed_envs":["dev","stg","prod"]}}}
```

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
.\scripts\hubctl.ps1 open
```

`install` registers a per-user Task Scheduler job at logon and starts the Hub. If another account can write to the checkout, it first restricts the checkout to you, SYSTEM, and Administrators, and it warns when the parent folder holding the child repositories is writable by others (see [security-and-logging.md](../security-and-logging.md)). The server listens only on `127.0.0.1:8765`. `open` starts the dashboard at `/dashboard/` using a one-use pairing token; it does not print the token. Use these commands for recovery and change activation:

```powershell
.\scripts\hubctl.ps1 status
.\scripts\hubctl.ps1 start
.\scripts\hubctl.ps1 restart
.\scripts\hubctl.ps1 stop
.\scripts\hubctl.ps1 open
.\scripts\hubctl.ps1 open -Ui    # restart with the activity UI (see below)
```

The per-user scheduled task `mcp-hub` runs the prepared environment through `pythonw.exe`, which starts the `mcp-hub-service.exe` process without a console. The Hub opens no window and stays in the background when a PowerShell or Windows Terminal window closes. An older registration named `mcp-hub-shared-service` ran `powershell.exe start.ps1 -Service`, which Windows Terminal shows as a tab; run `hubctl install` once to replace it. The task is allowed to start and continue on battery. Run `hubctl install` again after changing the Python version or task setup.

### Pre-start checks

`install`, `start`, `restart`, and `open` (when the Hub is stopped) run the same preparation before starting the task, so one command is enough on a fresh machine:

1. Bootstrap uv and Python into the repository if they are missing, then sync the locked Hub environment (`uv sync --locked`).
2. Run `python -m mcp_hub.preflight --install`. For each enabled child with a `prepare` block in `config/hub.example.json`, it checks the declared runtime and version (for example Node.js 22+ for `mcp-server-logs`, 20.12+ for `mcp-pr-companion`). A missing or outdated runtime is installed or upgraded with winget; Windows may show a UAC prompt. It then runs the child's own dependency step (`node start.mjs --preflight`, `node bin/cli.js prepare`) in the child repository.
3. Start the task, wait for `/health`, and print the status.

A child that still fails is reported (`preflight.check` in the Hub log) and the Hub starts with the remaining children. Child output is not printed; run the reported command in the child repository for details. `hubctl preflight` checks the runtimes only, without installing or starting anything, and exits non-zero when a requirement is not met:

```powershell
.\scripts\hubctl.ps1 preflight
```

The Hub service merges new `PATH` entries from the registry when it starts, so a runtime installed by winget is found without signing out.

The dashboard has service/child/agent state and a shared job list. Its language selector switches the UI between English (default) and Vietnamese; the choice is remembered in the browser. It polls every 10 seconds while its tab is visible and stops while the tab is hidden. Its stop and restart buttons invoke the same hidden `hubctl` helper. The helper sends an authenticated shutdown request; the Hub gives active HTTP requests up to 20 seconds to drain, then cancels them and closes its job runner and child MCP connections before the scheduled task exits. `hubctl` waits up to 45 seconds for a clean stop. If the service is stopped, use `hubctl start` or `hubctl open`; the dashboard cannot control a process that is not running. If the task is running but `/health` is unavailable, `hubctl stop` reports the condition rather than force-killing the task.

### Fast mode and the activity UI

By default the Hub starts in **fast mode**: a tool call goes route, audit log, child, with no activity tracking, no client tracking, and no database write. The dashboard then shows a hint instead of the barn, daily activity, and connected-clients panels.

The **activity UI** is an extension chosen at start. `hubctl start -Ui`, `restart -Ui`, `open -Ui`, and `install -Ui` write `config/private/launch.json` (`{"extensions": ["activity-ui"]}`, no secrets) just before starting the task; the service reads and deletes it at start. The next start without `-Ui`, including the logon-triggered one, is fast mode again; without `-Ui` the commands also remove a leftover file. `open -Ui` on a Hub running in fast mode prints a notice and restarts it with the UI. The dashboard's restart button keeps the current mode, and `hubctl status` prints it.

With the activity UI on, the pixel buffalo barn shows each child MCP at rest or handling tool calls, and the daily activity panel ranks MCPs by call count or total duration and lists the tools and labeled clients used, with filters by child, client, and tool. It reads only metadata retained for 30 days; it does not display tool arguments or results. Live events stream over Server-Sent Events while the tab is visible, and the daily report refreshes at most once per 3 seconds after calls finish. A background batch writer stores each finished call as one row (up to 50 rows or 1 second per write), so the call path never touches the database. Calls still running when the Hub stops are stored as interrupted.

### Connected AI clients

This panel and the `Clients:` line of `hubctl status` need the activity UI (`-Ui`); in fast mode the Hub does not track clients.

The dashboard panel **Connected AI clients** and `hubctl status` (`Clients:` line) show which AI clients use the shared Hub. 2026-07-28 MCP clients are stateless, so the Hub uses three signals, grouped by client name:

- **Live, stdio bridge:** each `start.ps1 -Bridge` process holds a `subscriptions/listen` stream open while it runs and reopens it within 5 seconds after a Hub restart. The client appears on connect and leaves when its bridge exits. A Hub in fast mode does not serve this stream; the bridge then stops holding it until the bridge restarts. The bridge sets no read timeout of its own, so a child call can run for the child's full `timeoutSeconds`.
- **Live, 2025-era HTTP session:** listed until the client deletes the session or it has been idle for 30 minutes.
- **Recently active:** any other authenticated request, such as a stateless direct-HTTP client, lists its client for 30 minutes after the last request. A closed bridge or session also stays in this state for 30 minutes.

Only client names and times are kept, in memory. Session IDs and tokens are never shown. A client configured with the legacy per-client launcher (`start.ps1` without `-Bridge`) runs its own Hub, so the shared service cannot see it.

## 4. Connect each AI client

Each client still has exactly one entry named `mcp-hub`:

- Prefer authenticated Streamable HTTP for clients that can run a local dynamic-header helper. The endpoint is `http://127.0.0.1:8765/mcp`; the helper is `scripts/http_headers.ps1`. Current Codex and Claude Code guides include this configuration.
- Use the stdio bridge for clients that cannot safely supply the bearer header. It starts with `scripts/start.ps1 -Bridge -ClientName "<AI_CLIENT>"` and forwards MCP protocol messages to the shared Hub. It reads the bearer token in process and labels calls in the daily dashboard report. HTTP clients are labeled from MCP `clientInfo`.
- The original `scripts/start.ps1` without `-Bridge` still launches an isolated stdio Hub for development or a temporary compatibility fallback. It does not share jobs with the service.

Follow [connecting-a-client.md](../connecting-a-client.md) and that client's guide. The setup agent must ask before editing a user-level client config and before removing an old direct child entry. Replace only child server entries that point to the logs/Jira/PR repos, then install/refresh the Hub rule and `/mcp-hub` command. This repository change does not modify any AI client settings.

## 5. Run and inspect an issue job

Start with `hub__list_projects`, which returns aliases and allowed environments but no local roots. `hub__submit_issue(project, question, env?, issue_key?, rcid?)` returns a `job_id` immediately, or an error when 20 jobs are already queued (try again after one starts or is cancelled). If both `env` and the project's default are absent, the job waits in `awaiting_input`; provide a valid environment with `hub__provide_job_input`. The coordinator dispatches only the fixed specialist roles. Each specialist sees only its role's read-only child tools and the encrypted cache refreshes the Hub allows (`jira__sync_ticket`, `pr__sync_my_open_prs`, `pr__get_pr_context_pack`); export and other write tools are not offered. Direct child tools remain available beside the job tools.

Inspect the job from any connected client with `hub__get_job(job_id)` or open the same job in the dashboard. State and evidence events are shared because all clients use the same Hub process and per-user SQLite database. `hub__get_job_detail` or the dashboard loads bounded pages of the encrypted full tool output only when requested.

Limits are two active jobs, 20 queued jobs, a 15-minute investigation window per job, and 30 child-tool calls per job. A job waiting for approval does not hold an active slot. A failed tool call is reported to the specialist, which continues. At the 15-minute limit, unfinished specialists stop, the coordinator gets up to 90 seconds to summarize the evidence already collected, and the job ends `partial`. Unconfigured providers or children produce a partial result; the Hub does not guess a missing environment, alias, or source.

## 6. Approve a pending write

A gated write (today: a forced `jira__sync_ticket` with `force=true`) waits in `awaiting_approval`. Open the job in the dashboard and inspect the tool name and its full arguments in the scrollable preview; only secret values such as tokens, keys, and passwords are masked, and a preview over 64 KB says it was cut. The dashboard can approve directly (it asks for confirmation and disables the button while the request runs), or copy the one-time code there and provide it to the AI client for `hub__approve_job(job_id, code)`. Only the paired dashboard can read the code or approve; the bearer token that AI clients hold gets `403` on those routes. Each write waits for its own approval. The code expires after 10 minutes and works once. The user must explicitly choose to approve; an agent saying “approved” is not enough.

Documented encrypted cache updates made automatically as part of read workflows do not prompt. Direct child calls keep their current behavior and must follow their child contract. If the service restarts while an investigation is running, the job is marked interrupted and model work is not replayed. One-time codes do not survive a restart; approve a still-pending write from the dashboard instead. A job still waiting for approval remains pending until 15 minutes after it was created, then becomes `partial` with event `approval.expired` (at start for older ones) and its write is not run. Approving it in time runs only its exact saved operation once and returns a partial job because the broader investigation does not resume.

## 7. Retention and support

SQLite job records, events, pending actions, summaries, tool results, and daily child-call history are removed after 30 days. Sensitive blobs are protected at rest with Windows user-scope DPAPI. The database belongs to the Windows account that created it and cannot be decrypted under another account. Hub audit logs remain metadata-only and are in `logs/mcp-hub.jsonl`.

| Symptom | Check |
|---|---|
| Service task runs but health is unavailable | `hubctl status`; inspect the redacted `config.load.failed` or child connection event in `logs/mcp-hub.jsonl`; a crash traceback is in `logs/mcp-hub-service.err`; run `uv run python scripts/smoke.py` |
| A child shows `reconnecting` or calls return `ChildUnavailable` | The child failed its last attempt or its session ended; the Hub retries it every 1 s up to 60 s. Find `server.disconnected`, `server.connect.failed`, or `server.reconnect.failed` for that `server` in `logs/mcp-hub.jsonl` (`error_type`, `retry_in_seconds`); the user can read the child's own output in `logs/children/<server-id>.log` (agents must not print it). A child that times out on first start (`error_type=TimeoutError`) may need a larger `connectTimeoutSeconds`. |
| A child stays `connecting` at start | Its first attempt is still running; startup waits at most its `connectTimeoutSeconds` (default 120). |
| HTTP client gets 401 | Restart after installing the service so `service.json` exists; reconnect so the client header helper runs again |
| Dashboard pairing is rejected | Run `hubctl open` again; pairing tokens expire after two minutes and are one-use |
| Source alias is unknown | Check `project-status`, then check the matching alias in the source child's private project config and restart |
| Agent shows unconfigured | Check `agent-status`; edit the provider/model/key in private `agents.json`, then restart |
| Agent shows "Provider package not installed" | The provider's SDK (`openai`, `anthropic`, or `google-genai`) is missing from the Hub environment; run `hubctl start` to resync the locked environment |
| Agent shows "Needs Windows DPAPI" | The Hub runs off Windows, where job details cannot be encrypted; `hub__submit_issue` refuses jobs there. Direct child tools still work |
| Write waits unexpectedly | Inspect the job's preview and state; a forced Jira re-sync is gated by design, and other writes are never offered to specialists |
| Submitting a job says the queue is full | 20 jobs are already queued; wait for one to start or cancel one in the dashboard |

Do not share private configuration files, job databases, or full tool results in issue reports. Report request IDs and redacted error metadata only.

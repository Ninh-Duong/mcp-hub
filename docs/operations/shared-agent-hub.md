# Shared Agent Hub operations

This is the operator process for one Windows user running a single Hub for several local AI clients. The first release changes code in **two repositories**: `mcp-hub` and the new sibling `mcp-read-source`. The existing `mcp-server-logs`, `mcp-jira-sync`, and `mcp-pr-companion` repositories keep their current tools and cache behavior; this integration does not edit their code.

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

`install` registers a per-user Task Scheduler job at logon and starts the Hub. The server listens only on `127.0.0.1:8765`. `open` starts the dashboard at `/dashboard/` using a one-use pairing token; it does not print the token. Use these commands for recovery and change activation:

```powershell
.\scripts\hubctl.ps1 status
.\scripts\hubctl.ps1 start
.\scripts\hubctl.ps1 restart
.\scripts\hubctl.ps1 stop
.\scripts\hubctl.ps1 open
```

The scheduled task runs the prepared environment through `pythonw.exe`, so the Hub stays in the background when a PowerShell or Windows Terminal window closes. The task is allowed to start and continue on battery. `hubctl install` prepares and synchronizes the locked environment before registering the task; `hubctl start` also prepares it when starting a stopped task. Run `hubctl install` again after changing the Python version or task setup.

The dashboard has live service/child/agent state and a shared job list. Its pixel buffalo barn shows each child MCP at rest or handling tool calls. The daily activity panel ranks MCPs by call count or total duration and lists the tools and labeled clients used, with filters by child, client, and tool. It reads only metadata retained for 30 days; it does not display tool arguments or results. It polls at one-second intervals while visible. Its stop and restart buttons invoke the same hidden `hubctl` helper. The helper sends an authenticated shutdown request; the Hub gives active HTTP requests up to 20 seconds to drain, then cancels them and closes its job runner and child MCP connections before the scheduled task exits. `hubctl` waits up to 45 seconds for a clean stop. If the service is stopped, use `hubctl start` or `hubctl open`; the dashboard cannot control a process that is not running. If the task is running but `/health` is unavailable, `hubctl stop` reports the condition rather than force-killing the task.

## 4. Connect each AI client

Each client still has exactly one entry named `mcp-hub`:

- Prefer authenticated Streamable HTTP for clients that can run a local dynamic-header helper. The endpoint is `http://127.0.0.1:8765/mcp`; the helper is `scripts/http_headers.ps1`. Current Codex and Claude Code guides include this configuration.
- Use the stdio bridge for clients that cannot safely supply the bearer header. It starts with `scripts/start.ps1 -Bridge -ClientName "<AI_CLIENT>"` and forwards MCP protocol messages to the shared Hub. It reads the bearer token in process and labels calls in the daily dashboard report. HTTP clients are labeled from MCP `clientInfo`.
- The original `scripts/start.ps1` without `-Bridge` still launches an isolated stdio Hub for development or a temporary compatibility fallback. It does not share jobs with the service.

Follow [connecting-a-client.md](../connecting-a-client.md) and that client's guide. The setup agent must ask before editing a user-level client config and before removing an old direct child entry. Replace only child server entries that point to the logs/Jira/PR repos, then install/refresh the Hub rule and `/mcp-hub` command. This repository change does not modify any AI client settings.

## 5. Run and inspect an issue job

Start with `hub__list_projects`, which returns aliases and allowed environments but no local roots. `hub__submit_issue(project, question, env?, issue_key?, rcid?)` returns a `job_id` immediately. If both `env` and the project's default are absent, the job waits in `awaiting_input`; provide a valid environment with `hub__provide_job_input`. The coordinator dispatches only the fixed specialist roles and their allowlisted child tools. Direct child tools remain available beside the job tools.

Inspect the job from any connected client with `hub__get_job(job_id)` or open the same job in the dashboard. State and evidence events are shared because all clients use the same Hub process and per-user SQLite database. `hub__get_job_detail` or the dashboard loads bounded pages of the encrypted full tool output only when requested.

Limits are two active jobs, 15 minutes per job, and 30 child-tool calls per job. Unconfigured providers or children produce a partial result; the Hub does not guess a missing environment, alias, or source.

## 6. Approve a pending write

Agent-initiated child writes wait in `awaiting_approval`. Open the job in the dashboard and inspect the tool name and redacted argument preview. The dashboard can approve directly, or copy the one-time code there and provide it to the AI client for `hub__approve_job(job_id, code)`. The code expires after 10 minutes and works once. The user must explicitly choose to approve; an agent saying “approved” is not enough.

Documented encrypted cache updates made automatically as part of read workflows do not prompt. Direct child calls keep their current behavior and must follow their child contract. If the service restarts while an investigation is running, the job is marked interrupted and model work is not replayed. A job still waiting for approval remains pending; approving it runs only its exact saved operation once and returns a partial job because the broader investigation does not resume.

## 7. Retention and support

SQLite job records, events, pending actions, summaries, tool results, and daily child-call history are removed after 30 days. Sensitive blobs are protected at rest with Windows user-scope DPAPI. The database belongs to the Windows account that created it and cannot be decrypted under another account. Hub audit logs remain metadata-only and are in `logs/mcp-hub.jsonl`.

| Symptom | Check |
|---|---|
| Service task runs but health is unavailable | `hubctl status`; inspect the redacted `config.load.failed` or child connection event in `logs/mcp-hub.jsonl`; run `uv run python scripts/smoke.py` |
| HTTP client gets 401 | Restart after installing the service so `service.json` exists; reconnect so the client header helper runs again |
| Dashboard pairing is rejected | Run `hubctl open` again; pairing tokens expire after two minutes and are one-use |
| Source alias is unknown | Check `project-status`, then check the matching alias in the source child's private project config and restart |
| Agent shows unconfigured | Check `agent-status`; edit the provider/model/key in private `agents.json`, then restart |
| Write waits unexpectedly | Inspect the job's preview and state; explicit exports and external writes are gated by design |

Do not share private configuration files, job databases, or full tool results in issue reports. Report request IDs and redacted error metadata only.

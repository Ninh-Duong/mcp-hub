# mcp-hub

Local, config-driven MCP gateway for AI agents. It can run as one shared Windows service for multiple clients, or keep its original per-client stdio mode. It launches child MCP servers over stdio, supervises and reconnects them, discovers their tools, exposes only allowlisted ones, routes calls (briefly caching read-only results), and writes metadata-only audit events to `logs/mcp-hub.jsonl`. The shared service adds bounded, asynchronous investigation jobs across the children and a localhost dashboard.

**Agents: read [AGENTS.md](AGENTS.md) first.** Its rules are mandatory.

## Run

Python 3.12 is the project runtime (`.python-version`, `requires-python >=3.12`). The launcher prepares the local prerequisites before starting.

**Support matrix.** The per-client stdio Hub works on Windows, macOS, and Linux. The shared service, the dashboard's service controls, investigation jobs (their data is encrypted with Windows DPAPI), and preflight's winget runtime install are Windows-only; off Windows, `hub__submit_issue` answers `investigation jobs require Windows DPAPI`. Details: [Packaging and QC → Support matrix](docs/packaging-and-qc.md#support-matrix).

Run directly, including when an AI client starts the Hub over stdio:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start.ps1
```

On Linux or macOS:

```sh
bash scripts/start.sh
```

### Shared Windows service

After preparing this repository and the desired child MCPs, follow [the shared Agent Hub runbook](docs/operations/shared-agent-hub.md). The service starts at sign-in through Task Scheduler, listens only on loopback, and shares child connections and investigation jobs across local AI clients.

```powershell
.\scripts\hubctl.ps1 install    # first time: register the task, then start
.\scripts\hubctl.ps1 start      # check and install prerequisites, then start
.\scripts\hubctl.ps1 status
.\scripts\hubctl.ps1 open
.\scripts\hubctl.ps1 open -Ui  # same, with the live activity UI (barn, daily activity, clients)
.\scripts\hubctl.ps1 app       # open the desktop status app
```

**Desktop status app.** `install` also registers a second logon task, `mcp-hub-status`. At every sign-in it opens a small window that shows:
- whether the Hub is running, and in which mode;
- whether each child MCP is connected;
- with `-Ui`, the connected AI clients.

The window has **Start Hub** and **Open dashboard** buttons, plus a light/dark theme switch and an EN/VI switch. Both choices are remembered. Turn autostart off with `Disable-ScheduledTask mcp-hub-status`. Design rules, states, and keys are in [docs/desktop-app.md](docs/desktop-app.md).

Every start first syncs the locked Python environment and runs the pre-start checks: each enabled child's runtime (Node.js is installed or upgraded with winget when needed) and its dependency step. `hubctl preflight` only reports them. See [Pre-start checks](docs/operations/shared-agent-hub.md#pre-start-checks).

Clients with a safe dynamic-header helper can connect over HTTP using `scripts/http_headers.ps1`. Other clients use the stdio bridge by passing `-Bridge -ClientName "<AI_CLIENT>"` to `scripts/start.ps1`. The existing stdio launcher remains available for development and per-client compatibility.

`hubctl open` opens the paired localhost dashboard, in English or Vietnamese (language selector in the top bar). The Hub starts in fast mode by default: tool calls are not tracked beyond the audit log. Add `-Ui` to `start`, `restart`, or `open` to run with the activity UI until the next start (see [Fast mode and the activity UI](docs/operations/shared-agent-hub.md#fast-mode-and-the-activity-ui)). With it, the pixel barn shows live child-tool activity; the daily panel ranks child MCPs and lists tool calls by client for the last 30 days. The report stores only tool/client names, timing, and call state. HTTP clients are labeled from MCP `clientInfo`; bridge entries can pass `-ClientName <AI_CLIENT>`. The **Connected AI clients** panel and `hubctl status` show which clients are connected now or were active in the last 30 minutes (see [Connected AI clients](docs/operations/shared-agent-hub.md#connected-ai-clients)).

If `uv` is missing, the launcher installs a pinned uv release under the ignored `.tools/` directory without changing the global `PATH`, after checking the installer's SHA-256 ([details](docs/packaging-and-qc.md#pinned-uv-installer)). It then installs Python from `.python-version` when needed and runs `uv run --locked`, which creates or synchronizes the project environment from `uv.lock`. The first run needs internet access and write access to the repository and uv-managed data/cache directories. If download or installation fails, the launcher exits with a diagnostic on stderr before starting the Hub. Stdout stays reserved for MCP protocol traffic.

To connect an AI client, paste this into the agent (replace `<MCP_HUB_PATH>` with this repo's absolute path). Other prompts (instructions only, rule refresh, daily use) are in [docs/connecting-a-client.md §0](docs/connecting-a-client.md#0-for-the-human-the-prompts).

```text
Read <MCP_HUB_PATH>/docs/connecting-a-client.md and follow every step to connect yourself to mcp-hub,
using the file for this client in <MCP_HUB_PATH>/docs/clients/: register one MCP server named mcp-hub,
replace any old entries for its child MCPs, install the always-on rule and the /mcp-hub command.
Ask me before writing global config or removing entries, and report what you changed at the end.
```

Daily use after that: `/mcp-hub <server-id> <task>` (Codex: `$mcp-hub …`), e.g. `/mcp-hub mcp-server-logs show the prod logs for service orders-api`. For a cross-system issue, use `/mcp-hub investigate <project-alias> <task>`; the Hub routes a bounded asynchronous job (shared service only, see [docs/operations/shared-agent-hub.md](docs/operations/shared-agent-hub.md)). Without the command installed, type `mcp-hub:<server-id> <task>` or ask for the investigation in plain language.

The Hub discovers four supported child repositories next to this repo at startup. `mcp-read-source` is a separate sibling repository. Paths in Hub configuration are relative to this repo, such as `../mcp-server-logs`; they are resolved only in memory. Optional `config/private/hub.json` and per-child private configs override defaults. `MCP_HUB_CONFIG` still selects an explicit private manifest.

## Default setup

The supported children and exposed tools are mapped in [config/hub.example.json](config/hub.example.json) and [config/mcps/](config/mcps/). Clone the repos you want into one parent folder, keeping these folder names:

```text
<parent>/
  mcp-hub/
  mcp-server-logs/
  mcp-jira-sync/
  mcp-pr-companion/
  mcp-read-source/
```

```powershell
cd <parent>
foreach ($repo in "mcp-hub", "mcp-server-logs", "mcp-jira-sync", "mcp-pr-companion") { git clone "<GIT_BASE>/$repo.git" }
```

Place the separate `mcp-read-source` repository beside the other four. Its clone URL depends on where the user hosts it.

Connect your client to `mcp-hub` using [the client guide](docs/connecting-a-client.md). No Hub config copy or enable step is needed for a fresh clone. Call `hub__status` to see how many child repos were detected and which are connected, disabled, or need credentials. A child without credentials may remain unavailable while the others work; the Hub does not expose a child's tools until it supplies their schemas. The client still needs a path to the Hub launcher because it can start from any working directory.

| Server ID | Repo | Exposed tools | Where the user fills credentials | Contract |
|---|---|---|---|---|
| `mcp-server-logs` | `../mcp-server-logs` | `logs__*` | `openobserve` / `seq` in `config/private/mcps/mcp-server-logs.json`, else the child's `config/openobserve.<env>.env` and `config/seq.env` | [docs/mcps/mcp-server-logs.md](docs/mcps/mcp-server-logs.md) |
| `mcp-jira-sync` | `../mcp-jira-sync` | `jira__*` | `jira` in `config/private/mcps/mcp-jira-sync.json`, else the child's `.env` | [docs/mcps/mcp-jira-sync.md](docs/mcps/mcp-jira-sync.md) |
| `mcp-pr-companion` | `../mcp-pr-companion` | `pr__*` | `bitbucket` in `config/private/mcps/mcp-pr-companion.json`, else the child's `.env` | [docs/mcps/mcp-pr-companion.md](docs/mcps/mcp-pr-companion.md) |
| `mcp-read-source` | `../mcp-read-source` | `source__*` | Project roots by alias in the child's `config/private/projects.json` | [docs/mcps/mcp-read-source.md](docs/mcps/mcp-read-source.md) |

For logs/Jira/PR credentials, each value is taken from the first place that has it: the child config's `env`, then the Hub section (copy the template to `config/private/mcps/<id>.json` and fill it by hand), then the child's own files. Every child's check tool reports which one it used; see [Configuration sources](docs/child-mcp-compatibility.md#configuration-sources). Source roots are registered separately by alias and have no upstream credentials.

Prepare each child once:

- `mcp-server-logs`: `node start.mjs --preflight` in the child repo (Node 22+; installs, builds, creates the account files).
- `mcp-jira-sync`: nothing to install there; it runs on this repo's venv, so `uv sync` here covers its dependencies. Do not `pip install` into another Python.
- `mcp-pr-companion`: `node bin/cli.js prepare` in the child repo (Node 20.12+).
- `mcp-read-source`: no upstream account or build step; register local roots by alias in its private config as described in its contract.

Then fill each child's credentials and run `uv run python scripts/smoke.py`. Without a private child config, Jira starts from its template and falls back to the child's own local files; copy the template only to override them, without printing secrets. In automatic mode, the Hub selects this repo's `.venv` Python for Jira on each platform. To disable a detected child, create a minimal `config/private/hub.json` such as `{"version":1,"servers":{"mcp-jira-sync":{"enabled":false}},"features":{}}`. Private fields override defaults; `enabled: false` wins over discovery. Private configuration is never generated automatically.

## Check connections

```powershell
uv run python scripts/smoke.py
```

It starts the Hub as an MCP host would and prints one line per enabled child MCP. `hub__status` also lists detected children that are disabled or need credentials:

```text
mcp-jira-sync  PASS  5 tools, jira__check_connection ok
mcp-server-logs   PASS  12 tools, logs__list_openobserve_organizations dev=ok stg=ok prod=ok
```

A `FAIL` line names the cause:

| Output | Meaning |
|---|---|
| `not connected: <event> (<error_type>)` | The child didn't start or didn't answer within its `connectTimeoutSeconds` (`TimeoutError`); check `cwd`/`command`. The user can read the child's own output in `logs/children/<server-id>.log`. |
| `not connected (no Hub events; see logs)` | No events from this run (rare, e.g. log rotation); rerun. |
| `no exposed tools: <tools>` / `tool not found: <tools>` | Wrong tool names in `hub.json`. |
| `check tool not exposed: <tool>` | The `CHECKS` tool in `scripts/smoke.py` is not allowlisted. |
| `<tool> <env>=failed` | The child is up but can't reach that upstream environment; check its local account file. |
| `smoke=FAIL (<type>: Hub config is invalid …)` | `hub.json` or a child config is invalid; see `config.load.failed` in the log. |

A child without a `CHECKS` entry gets `PASS  N tools, connected (no read-only check in CHECKS)`. With no ready children, smoke exits successfully and the Hub still exposes `hub__status`. For checked children, exit code 0 means all checks passed; a failed upstream credential check means that child is not ready, not that sibling discovery failed. Tool responses and error texts are never printed. Full details are in `logs/mcp-hub.jsonl`.

## Package QC

Run the offline package checks before using or releasing a checkout:

```powershell
uv run --locked --python 3.12 python scripts/package_qc.py
```

The command runs, stopping at the first failure: `ruff` lint, the unit tests under `coverage`, the coverage gate (`--fail-under=85`), `check_leaks.py`, `check_code_standards.py`, and `compileall`. It does not contact child MCPs or upstream services. Set `MCP_HUB_E2E=1` to also run the launcher end-to-end test in a fresh uv environment (needs network; CI sets it). Add `--live-smoke` only when private Hub configuration and required child credentials are available. See [Packaging and QC](docs/packaging-and-qc.md) for bootstrap behavior and CI details.

## Where to look

| Need | File |
|------|------|
| Rules for agents | [AGENTS.md](AGENTS.md) |
| Connect an AI client (Antigravity, Claude Code, Cursor, …) to the Hub | [docs/connecting-a-client.md](docs/connecting-a-client.md) |
| Per-client config: MCP entry, always-on rule, `/mcp-hub` command | [docs/clients/](docs/clients/) |
| Rules a client's agent follows when using the Hub (`mcp-hub:<server-id> <task>`) | [docs/client-rules.md](docs/client-rules.md) |
| Add or reconfigure a child MCP | [docs/adding-a-child-mcp.md](docs/adding-a-child-mcp.md) |
| Change child MCP code and keep it compatible with the Hub | [docs/child-mcp-compatibility.md](docs/child-mcp-compatibility.md) |
| What a child MCP does, its tools and side effects | [docs/mcps/](docs/mcps/) |
| Module map, request flow, failure handling, prompts, job limits | [docs/architecture.md](docs/architecture.md) |
| Bootstrap, support matrix, QC steps, CI | [docs/packaging-and-qc.md](docs/packaging-and-qc.md) |
| Design and plan history (not maintained; the docs above are current) | [docs/history/](docs/history/) |
| Shared Windows service setup, operation and client migration | [docs/operations/shared-agent-hub.md](docs/operations/shared-agent-hub.md) |
| Desktop status app: behavior, design keywords, states, locale keys | [docs/desktop-app.md](docs/desktop-app.md) |
| Secrets, audit events, diagnosing failures | [docs/security-and-logging.md](docs/security-and-logging.md) |
| Safe config templates | [config/hub.example.json](config/hub.example.json), [config/mcps/](config/mcps/) |

## Layout

- `src/mcp_hub/`: the Hub package; [docs/architecture.md → Module layout](docs/architecture.md#module-layout) lists every module and the dependency direction. `desktop/` is the desktop status app and `control.py` the shared background `hubctl` launcher.
- `config/`: tracked templates (`hub.example.json`, `mcps/*.example.json`); `config/private/` holds the user's ignored files (see [AGENTS.md → Private files](AGENTS.md#private-files)).
- `scripts/`: launchers (`start.ps1`, `start.sh`), service control (`hubctl.ps1`, `service_launcher.pyw`, `http_headers.ps1`), `smoke.py`, `package_qc.py`, `check_leaks.py`, `check_code_standards.py`, `private_config.py` (inspect private configs without printing secrets), and `bench_call_path.py` (tool-call latency with the activity UI off and on).
- `tests/`: offline unit tests, run by `package_qc.py` (or `uv run python -m unittest discover tests`).
- `logs/`: the redacted audit log `mcp-hub.jsonl`, child stderr under `children/`, and service stderr; all ignored.

## Security summary

- `config/private/`, `.local/`, `.env`, `.env.*`, and `logs/` are gitignored and never committed.
- Tool arguments and successful results are not logged. Values under sensitive config keys (including sensitive `env` entries) and inherited environment variables with sensitive names are redacted from Hub error logs.
- A child receives only the MCP SDK's default safe variables (e.g. `PATH`, `APPDATA`, `USERNAME`; on POSIX, `HOME`, `USER`), its `inheritEnv` variables, and its configured `env`.
- stdout is reserved for MCP protocol frames.

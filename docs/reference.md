# Reference: environment variables and HTTP routes

This is the one list of both. `scripts/check_docs.py` (part of `package_qc.py`) fails when the code has an environment variable or a route that is not listed here, or this page lists one the code no longer has.

## Environment variables

| Variable | Read by | Effect |
|---|---|---|
| `MCP_HUB_CONFIG` | `config.py` | Use this Hub config file instead of merging `config/private/hub.json` with the defaults. It must be inside `config/private/`, and every server in it must be `"enabled": true`. The desktop app and the settings API then show the settings read-only. |
| `MCP_HUB_PRIVATE_DIR` | `config.py`, `hubctl.ps1`, `desktop/connection.py` | Move `config/private/` (tests and tools). The connection file then lives in that folder, so tests never replace the real one. |
| `MCP_HUB_LOG_DIR` | `audit.py` | Move `logs/` (the audit log and child stderr logs; tests and tools). |
| `MCP_HUB_CONNECTION_FILE` | `service_config.py`, `hubctl.ps1`, `desktop/connection.py` | Path of the connection file (default `%LOCALAPPDATA%\mcp-hub\connection.json`). |
| `MCP_HUB_CLIENT_NAME` | `bridge.py` | The AI client's name the stdio bridge reports; `start.ps1 -Bridge -ClientName <name>` sets it. Default `unknown`. |
| `MCP_HUB_E2E` | `tests/test_stdio.py` | `1` runs the launcher test that needs the network (CI sets it). |
| `MCP_HUB_STATUS_ICON` | `scripts/build_desktop.py`, `packaging/desktop/mcp-hub-status.spec` | Build only: the drawn app icon passed to PyInstaller. |

## HTTP routes

The shared service listens on `127.0.0.1` and `settings.http.port` (default 8765). Who may call a route is decided by `LocalBearerMiddleware` (`http.py`), before the route runs:

- **Client token:** the AI clients' token in `service.json`.
- **Admin token:** new at every start; it is in the connection file only.
- **Session:** a paired browser's `hub_session` cookie. It is `HttpOnly`, `SameSite=Strict`, on path `/dashboard`, and lasts `settings.dashboard.sessionHours`.

| Method | Path | Who | What |
|---|---|---|---|
| GET | `/health` | anyone | `{"ok": true}` only |
| GET, POST, DELETE | `/mcp` | client token | The MCP Streamable HTTP endpoint |
| GET | `/dashboard`, `/dashboard/` | session, admin token, or a one-use `?pair=` token | The dashboard page; `?pair=` sets the session cookie |
| GET | `/dashboard/{asset}` | session, admin token | Static files from an allowlist (404 otherwise) |
| POST | `/dashboard/pair` | admin token, session | `{pairing_token, expires_in_seconds}`: a one-use link token for the browser (`hubctl open`, the desktop app) |
| GET | `/dashboard/api/status` | admin token, session | Hub and child states, and the activity UI's clients and today's counts when it runs |
| GET | `/dashboard/api/jobs` | admin token, session | Job list (`limit` up to 500, `offset`) |
| GET | `/dashboard/api/jobs/{job_id}` | admin token, session | One job's state, summary, evidence references, and timeline |
| GET | `/dashboard/api/jobs/{job_id}/details/{source_id}` | admin token, session | One page of a stored, encrypted result (`offset`, `limit` up to 10000) |
| GET | `/dashboard/api/jobs/{job_id}/pending-call` | session only | The pending write's tool and arguments, secrets masked |
| GET | `/dashboard/api/jobs/{job_id}/approval-code` | session only | The one-time approval code |
| POST | `/dashboard/api/jobs/{job_id}/approve` | session only | Approve the pending write |
| POST | `/dashboard/api/jobs/{job_id}/cancel` | admin token, session | Cancel a job (409 when it can no longer be cancelled) |
| POST | `/dashboard/api/control/{command}` | admin token, session | `restart` or `stop` through `hubctl` in the background (404 for another command, 503 off Windows) |
| POST | `/dashboard/api/service/shutdown` | admin token, session | Graceful stop; `hubctl stop` uses it |
| GET | `/dashboard/api/settings` | admin token only | Children on/off, tracking, and whether the config is editable: booleans and IDs only |
| POST | `/dashboard/api/settings/tracking` | admin token only | `{"enabled": bool}`: the activity UI at the next start |
| POST | `/dashboard/api/settings/servers/{server_id}` | admin token only | `{"enabled": bool}` for one child (404 unknown, 409 with `MCP_HUB_CONFIG`) |
| GET, POST | `/dashboard/api/settings/autostart` | admin token only | The Hub's sign-in task, `{"hub": bool}` (503 when `hubctl` fails) |
| GET | `/dashboard/api/activity` | admin token, session | Activity UI only: one day's calls (`date`, `limit`, `offset`, filters) |
| GET | `/dashboard/api/activity/stream` | admin token, session | Activity UI only: live call events (server-sent events, keep-alive every 15 s) |

The client token gets `403` on every `/dashboard` route. The admin token gets `403` on the three approval routes; see [security-and-logging.md](security-and-logging.md).


## MCP tools

| Tool | Who | What |
|---|---|---|
| `hub__get_full_result` | client token | Retrieve a page of an original, un-condensed result by its `result_id` (`offset`, `limit` up to 50000). Exists only with the `bitnet` extension. |

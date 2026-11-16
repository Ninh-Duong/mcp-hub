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

The shared service listens on `127.0.0.1` and `settings.http.port` (default 8765). `LocalBearerMiddleware` (`http.py`) checks the token before a route runs:

- **Client token:** the AI clients' token in `service.json`.
- **Admin token:** new at every start; it is in the connection file only, for the desktop app and local Hub controls.

| Method | Path | Who | What |
|---|---|---|---|
| GET | `/health` | anyone | `{"ok": true}` only |
| GET, POST, DELETE | `/mcp` | client token | The MCP Streamable HTTP endpoint |
| GET | `/dashboard/api/status` | admin token | Hub and child states, plus activity and extension state when enabled |
| POST | `/dashboard/api/control/{command}` | admin token | `restart` or `stop` through `hubctl` in the background (404 for another command, 503 off Windows) |
| POST | `/dashboard/api/service/shutdown` | admin token | Graceful stop; `hubctl stop` uses it |
| GET | `/dashboard/api/settings` | admin token only | Children on/off, tracking, and whether the config is editable: booleans and IDs only |
| POST | `/dashboard/api/settings/tracking` | admin token only | `{"enabled": bool}`: the activity UI at the next start |
| POST | `/dashboard/api/settings/bitnet` | admin token only | `{"enabled": bool}`: the BitNet extension at the next start |
| POST | `/dashboard/api/bitnet/install` | admin token only | Start the BitNet install in the background (`202` with the `bitnet` status block; `409` when installed); see [bitnet.md](bitnet.md#install-from-the-desktop-app) |
| GET | `/dashboard/api/bitnet/processing/stream` | admin token | BitNet only: transient processing events and last-three snapshots over SSE; see [bitnet.md](bitnet.md#processing-stream) |
| POST | `/dashboard/api/settings/servers/{server_id}` | admin token only | `{"enabled": bool}` for one child (404 unknown, 409 with `MCP_HUB_CONFIG`) |
| POST | `/dashboard/api/settings/servers/{server_id}/config-file` | admin token only | Create the child's private config from its template if missing; returns `{"path"}` (404 unknown, 409 with `MCP_HUB_CONFIG`, 500 when the file cannot be written) |
| GET, POST | `/dashboard/api/settings/autostart` | admin token only | The Hub's sign-in task, `{"hub": bool}` (503 when `hubctl` fails) |
| GET | `/dashboard/api/update` | admin token only | Current bounded update state and sanitized component events |
| POST | `/dashboard/api/update/check` | admin token only | Start asynchronous update discovery; returns update state |
| POST | `/dashboard/api/update` | admin token only | Start the full update; 409 while another update is running |
| POST | `/dashboard/api/update/retry` | admin token only | `{"components": [id, ...]}`: retry failed components from the latest run |
| GET | `/dashboard/api/activity` | admin token | Activity UI only: one day's calls (`date`, `limit`, `offset`, filters) |
| GET | `/dashboard/api/activity/stream` | admin token | Activity UI only: live call events (server-sent events, keep-alive every 15 s) |

The client token gets `403` on every `/dashboard` API route. Job inspection and write approvals remain MCP job features; no browser interface is provided.


## MCP tools

| Tool | Who | What |
|---|---|---|
| `hub__get_full_result` | client token | Retrieve full or filtered (`contains`, `regex`, `fields`) pages from condensed results by `result_id` or `result_ids` (default raw text, or `format="json"`; works standalone and in `hub__batch`). |

# Interface Contracts: Gateway Hardening & Protocol Evolution

## 1. Admin HTTP API (`src/mcp_hub/dashboard/api.py`, `settings_api.py`)

All routes below require the Hub `admin_token` (`LocalBearerMiddleware` returns `403` to `client_token` and `401` when unauthenticated):

| Method & Path | Request Body | Response (`200`) | Purpose |
|---|---|---|---|
| `GET /dashboard/api/jobs/pending` | — | `{"pending": [{"job_id", "tool_name", "agent", "expires_at", "argument_keys"}]}` | List investigation writes waiting for human approval |
| `POST /dashboard/api/jobs/{job_id}/decision` | `{"action": "approve" \| "reject"}` | `{"job_id", "action", "applied": bool}` | Approve or reject a pending job write |
| `POST /dashboard/api/security/pins/accept` | `{"server_id"?: str}` | `{"accepted": [{"server", "tool"}]}` | Accept current tool definitions for one or all connected children |
| `POST /dashboard/api/settings/servers/{server_id}` | `{"enabled": bool}` | Settings payload + `"reloaded": bool` | Persist server switch and hot-reload that child's `ChildSupervisor` |

## 2. `hubctl` Commands (`scripts/hubctl.ps1`)

- `hubctl pending-jobs` — calls `GET /dashboard/api/jobs/pending` and displays waiting approvals.
- `hubctl approve-job -JobId <id>` / `hubctl reject-job -JobId <id>` — calls `POST /dashboard/api/jobs/<id>/decision`.
- `hubctl accept-pins [-ServerId <id>]` — calls `POST /dashboard/api/security/pins/accept`.

## 3. MCP Protocol Surface (`src/mcp_hub/server.py`, `resources.py`, `bridge_proxy.py`)

- `tools/list`:
  - Under `protocol.toolExposure == "all"`: unchanged full allowlisted set.
  - Under `protocol.toolExposure == "planned"`: Hub orchestration tools (`hub__*`) + tools belonging to `PlanBook.active_servers()`. Creating a plan that activates new servers publishes `notifications/tools/list_changed`.
- `resources/list`, `resources/templates/list`, `resources/read`:
  - Aggregates connected children's resources under `mcp-hub://<server-id>/<encoded-uri>`.
  - Forwarded identically through `mcp-hub-bridge` (`src/mcp_hub/bridge_proxy.py`).

## 4. Audit Events (`src/mcp_hub/audit_events.py`, `docs/security-and-logging.md`)

- `tool.pins.accepted`: `server`, `count` (never tool definitions).
- `resource.read.started` / `resource.read.succeeded` / `resource.read.failed`: `request_id`, `server`, `duration_ms`, optional `error_type`.
- `server.reloaded`: `server`, `enabled`.
- `plans.snapshot.invalid`: `error_type` (WARNING when persisted `plans.json` cannot be decoded).

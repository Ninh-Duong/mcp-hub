# Configuration templates

This folder holds tracked templates for `mcp-hub`. Real local configurations live in the ignored `config/private/` directory and are never committed.

## Files in `config/`

| Template | Private target | Purpose |
|---|---|---|
| `hub.example.json` | `config/private/hub.json` (optional overrides) | Default curated child MCPs, `repository` URLs cloned by `Setup.cmd`, `prepare` prerequisites, tool allowlists, and `cacheSeconds` defaults. Loaded at runtime and merged with `config/private/hub.json`. |
| `projects.example.json` | `config/private/projects.json` | Safe empty starter (`{"version": 1, "projects": {}}`) valid immediately after copying. |
| `projects.full.example.json` | `config/private/projects.json` (reference) | Complete schema example showing `default_env`, `allowed_envs`, `logs` targets, and optional `sql` database hints. |
| `agents.example.json` | `config/private/agents.json` | Model provider, model name, and API key for each investigation role (`coordinator`, `logs`, `source`, `jira`, `pr`, `sql`). |
| `client_profiles.example.json` | `config/private/client_profiles.json` (optional override) | Per-AI-client execution modes (`multi_agent` / `single_agent`) and allowed orchestrate/execute models used by `hub__plan`. |
| `mcps/mcp-*.example.json` | `config/private/mcps/<server-id>.json` (optional override) | Launch settings and empty credential sections (`openobserve`/`seq`, `jira`, `bitbucket`, `mssql`) for each default child MCP. |
| `mcps/example.example.json` | `config/private/mcps/<id>.json` | Minimal `stdio` template for adding a custom child MCP ([adding-a-child-mcp.md](../docs/adding-a-child-mcp.md)). |
| `mcps/example-http.example.json` | `config/private/mcps/<id>.json` | Minimal `streamable-http` template for adding a remote HTTPS or loopback HTTP child MCP. |

## Important: `config/private/hub.json` overlay rules

In default mode (when `MCP_HUB_CONFIG` is not set), `src/mcp_hub/config.py` merges `config/private/hub.json` over `config/hub.example.json` by top-level server and feature key:

- Keep `config/private/hub.json` minimal (for example `servers.<id>.enabled`, `extensions`, and optional `settings`).
- **Avoid copying `features.<name>.tools` into `config/private/hub.json`** unless you intentionally want a custom tool list: overriding `tools` replaces the entire default list from `hub.example.json`, which drops `cacheSeconds` TTLs and hides newly added child tools.
- Optional top-level `settings` tunables are documented in [docs/settings.md](../docs/settings.md). Optional `extensions` accepts `"activity-ui"` and `"bitnet"` (managed by the desktop app).

Minimal `config/private/hub.json` example:

```json
{
  "version": 1,
  "servers": {
    "mcp-sqlserver": { "enabled": false }
  },
  "extensions": ["activity-ui"],
  "settings": {
    "http": { "port": 8765 },
    "policy": { "mode": "enforce" }
  }
}
```

## Important: Hub `projects.json` vs `mcp-read-source` `projects.json`

Both repositories use a private `projects.json`, with **different value shapes**:

- **Hub (`mcp-hub/config/private/projects.json`)** maps each project alias to an **object** (`default_env`, `allowed_envs`, `logs`, `sql` — never local paths); see [projects.full.example.json](projects.full.example.json).
- **Source child (`../mcp-read-source/config/private/projects.json`)** maps the same alias to a **local folder path string**:

  ```json
  {
    "version": 1,
    "projects": {
      "example-service": "<LOCAL_REPOSITORY_ROOT>"
    }
  }
  ```

Putting a plain path string inside `mcp-hub/config/private/projects.json` causes Hub startup to fail with `ProjectConfigError: Project '<alias>' must be an object`.

## Inspecting private configs safely

Never print private config files containing secrets or roots. Use the status helpers instead:

```powershell
uv run python scripts/private_config.py project-status
uv run python scripts/private_config.py agent-status
uv run python scripts/private_config.py client-status
uv run python scripts/private_config.py keys <server-id>
```

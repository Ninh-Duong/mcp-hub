# Adding a child MCP

Agent runbook. No Hub code change is needed. Follow every step in order and obey [AGENTS.md](../AGENTS.md). In the examples, `<id>` is the server ID: lowercase, kebab-case, matching the child repository (e.g. `jira-sync-ticket`).

## 1. Gather facts (from the child repo, not from private files)

- How the child starts over stdio: interpreter or executable, arguments, and working directory. The repo must sit next to `mcp-hub`, so its path is `../<repo>`.
- Its tool names. List them from the child's code or docs; every allowlisted name must match exactly.
- The env variable names and config keys it needs, and which of them are secrets.
- Each tool's side effects: read-only or write, and what it writes.

## 2. Create the private child config

Copy the template, then fill in the non-secret fields:

```powershell
Copy-Item config/mcps/example.example.json config/private/mcps/<id>.json
uv run python scripts/private_config.py set <id> cwd '"../<repo>"'
uv run python scripts/private_config.py set <id> command '".venv/Scripts/python.exe"'
uv run python scripts/private_config.py set <id> args '["server.py"]'
uv run python scripts/private_config.py set <id> env.SOME_API_TOKEN '"<SET_BY_USER>"'
uv run python scripts/private_config.py keys <id>
```

Never open the file after creating it. The `set` value is JSON, so strings need inner quotes. `set` refuses a real value for any key that looks sensitive (`token`, `secret`, `password`, `authorization`, `email`, `credential`). If a child-specific template exists in `config/mcps/`, copy that one instead.

| Key | Required | Default | Notes |
|-----|----------|---------|-------|
| `transport` | yes | none | Must be `"stdio"`. |
| `cwd` | yes | none | Relative paths resolve against the `mcp-hub` root, e.g. `../<repo>`. Must exist. |
| `command` | yes | none | A relative path containing `/` resolves against `cwd`, e.g. `.venv/Scripts/python.exe`. A bare name (`python`, `node`, `uv`) is looked up on `PATH`. |
| `args` | no | `[]` | A list of strings, passed verbatim. No secrets. |
| `inheritEnv` | no | `PATH`, `SYSTEMROOT`, `WINDIR`, `TEMP`, `TMP`, `USERPROFILE` | The only Hub process env vars the child receives. |
| `env` | no | `{}` | String values passed verbatim. A path inside `env` is resolved by the child, normally against its `cwd`, so use a path relative to the child repo (e.g. `../mcp-hub/config/private/mcps/<id>.json`). All values are redacted from Hub error logs. |
| `timeoutSeconds` | no | `60` | Limit for each tool call. |
| any other key | no | none | Ignored by the Hub; children may read them (e.g. the `jira` block). Values under sensitive-looking keys are redacted from logs. |

## 3. Register it in `config/private/hub.json`

Add the server **disabled** and allowlist only the tools that are needed:

```json
"servers": {
  "<id>": { "enabled": false, "configFile": "config/private/mcps/<id>.json" }
},
"features": {
  "<feature-name>": {
    "enabled": true,
    "tools": [ { "server": "<id>", "name": "<child_tool>", "exposeAs": "<short>__<child_tool>" } ]
  }
}
```

- Merge these entries into the existing objects. Never remove other servers or features.
- `configFile` must be inside `config/private/`.
- `exposeAs` is optional and defaults to `<id>__<tool>`. It must match `[A-Za-z0-9_-]{1,128}` and be unique across the Hub. A short prefix is fine (Jira uses `jira__`).
- A feature that references a disabled server makes the Hub refuse to start. Keep the new feature `"enabled": false` too, until step 5.

## 4. Write the contract

Create `docs/mcps/<id>.md` from the template in [mcps/README.md](mcps/README.md). The Hub serves this file to clients as the `<id>` prompt, so write it for an agent that will call the tools.

## 5. Hand off secrets, then enable

1. Ask the user to replace every `<SET_BY_USER>` in `config/private/mcps/<id>.json`, and list the key names (from `keys <id>`). Never ask for the values in chat.
2. After the user confirms, set `"enabled": true` for the server and the feature in `hub.json`.
3. Restart the MCP client that runs the Hub (`scripts/start.ps1` / `scripts/start.sh`, see README → Run). Config is read only at startup.

## 6. Verify

Check the newest events in `logs/mcp-hub.jsonl`:

| You see | Meaning | Fix |
|---------|---------|-----|
| `server.connected` (server `<id>`) + `hub.tools.registered` listing your tools | Working | Done |
| `config.load.failed` with "unfilled `<SET_BY_USER>`" | Secrets not filled | Back to step 5.1 |
| `config.load.failed` with other text | Invalid config (message names the key) | Fix it with `private_config.py set` or in `hub.json` |
| `server.connect.failed` / `server.list_tools.failed` | Child didn't start or answer | Check `cwd`, `command`, `args`, and the child's own logs |
| `route.skipped` with `tool not found` | Wrong tool name in the allowlist | Fix `name` in `hub.json` |

Add one line for the child to `CHECKS` in `scripts/smoke.py`, using a read-only tool that proves the child reaches its upstream service. Then run the definition-of-done commands in [AGENTS.md](../AGENTS.md). `smoke.py` prints `NO_CHECK` for an enabled child that has no entry.

If the child keeps its credentials in its own repo (as `mcp-server-logs` does), the Hub config has no `<SET_BY_USER>` fields and step 5.1 is skipped. Ask the user to fill the child's own account files instead.

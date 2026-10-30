# Adding a child MCP

Agent runbook. The four built-in children are discovered automatically when cloned as siblings; this procedure is for adding another child or overriding a built-in child. A new child needs a curated Hub entry and tool allowlist; directory scanning alone does not register arbitrary MCPs. Follow every step in order and obey [AGENTS.md](../AGENTS.md). Before changing child source for Hub compatibility, also follow [the child compatibility contract](child-mcp-compatibility.md). In the examples, `<id>` is the server ID: lowercase, kebab-case, matching the child repository (e.g. `mcp-jira-sync`).

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

Never open the file after creating it. The `set` value is JSON, so strings need inner quotes. `set` refuses a real value for any key that looks sensitive (`token`, `secret`, `password`, `authorization`, `email`, `credential`, `api_key`, or ending in `key`/`_key`). If a child-specific template exists in `config/mcps/`, copy that one instead.

| Key | Required | Default | Notes |
|-----|----------|---------|-------|
| `transport` | yes | none | Must be `"stdio"`. |
| `cwd` | yes | none | Relative paths resolve against the `mcp-hub` root, e.g. `../<repo>`. Must exist. |
| `command` | yes | none | A relative path containing `/` resolves against `cwd`, e.g. `.venv/Scripts/python.exe` (Windows) or `.venv/bin/python` (macOS/Linux; templates show the Windows form). A bare name (`python`, `node`, `uv`) is looked up on `PATH`. |
| `args` | no | `[]` | A list of strings, passed verbatim. No secrets. |
| `inheritEnv` | no | `PATH`, `SYSTEMROOT`, `WINDIR`, `TEMP`, `TMP`, `USERPROFILE` | Hub process env vars passed to the child, in addition to the MCP SDK's default safe set (e.g. `PATH`, `APPDATA`, `USERNAME`; POSIX: `HOME`, `USER`). No other Hub env vars reach the child. |
| `env` | no | `{}` | String values passed verbatim. A path inside `env` is resolved by the child, normally against its `cwd`, so use a path relative to the child repo (e.g. `../mcp-hub/config/private/mcps/<id>.json`). All values are redacted from Hub error logs. |
| `timeoutSeconds` | no | `60` | Positive finite number of seconds for each tool call. |
| `connectTimeoutSeconds` | no | `120` | Positive finite number of seconds to start the child, finish the MCP handshake, and list its tools, at Hub start and on each reconnect. Raise it for a child whose first start builds or installs dependencies. |
| any other key | no | none | Ignored by the Hub; children may read them (e.g. the `jira` block). Values under a sensitive key, including nested string values, are redacted from Hub error logs. Sensitive keys include `api_key`, keys ending in `_key` (e.g. `ai_context_key`), and common token, password, authorization, email, and credential names. |

The Hub config version must be the integer `1`. Values of `inheritEnv` variables with sensitive names (token, secret, password, API key, ...), and child `env` values under sensitive keys, are added to the Hub's exact-value error redaction set; give a secret variable such a name.

## 3. Register it in `config/private/hub.json`

Add a new server **disabled** and allowlist only the tools that are needed. For a built-in child, copy the example only when you want to override its default settings; an explicit `enabled: false` disables automatic discovery:

```json
"servers": {
  "<id>": { "enabled": false, "configFile": "config/private/mcps/<id>.json" }
},
"features": {
  "<feature-name>": {
    "enabled": false,
    "tools": [ { "server": "<id>", "name": "<child_tool>", "exposeAs": "<short>__<child_tool>" } ]
  }
}
```

- Merge these entries into the existing objects. Never remove other servers or features.
- `configFile` must be inside `config/private/`.
- `exposeAs` is optional and defaults to `<id>__<tool>`. It must match `[A-Za-z0-9_-]{1,128}` and be unique across the Hub. A short prefix is fine (Jira uses `jira__`).
- A feature that references a disabled server makes the Hub refuse to start. Keep the new feature `"enabled": false` too, until step 5.

### Declare start prerequisites (`prepare`)

When the child needs a system runtime or a dependency/build step, declare it once in the child's server entry in the tracked `config/hub.example.json`. Do not add install steps to scripts or docs elsewhere. The pre-start check (`python -m mcp_hub.preflight`, run by `hubctl start`) reads it for every enabled child:

```json
"<id>": {
  "enabled": false,
  "configFile": "config/private/mcps/<id>.json",
  "prepare": {
    "runtime": { "name": "node", "minVersion": "22.0.0", "winget": "OpenJS.NodeJS.LTS" },
    "command": ["node", "start.mjs", "--preflight"],
    "timeoutSeconds": 900
  }
}
```

- `runtime` is optional. `name` is the executable looked up on `PATH` and queried with `--version`; `minVersion` is `MAJOR.MINOR[.PATCH]`; `winget` is the package ID used to install or upgrade it on Windows.
- `command` is required: an argument array run in the child's `cwd` with the child's `env`. It must be idempotent and quick when nothing changed, and it must not print secrets. The Hub does not show its output.
- `timeoutSeconds` is optional (default 900).
- A child that runs on the Hub's own virtual environment needs no `prepare`; add its Python packages to `pyproject.toml` instead.

## 4. Write the contract

Create `docs/mcps/<id>.md` from the template in [mcps/README.md](mcps/README.md). The Hub serves this file to clients as the `<id>` prompt, so write it for an agent that will call the tools. Include the read-only smoke tool and its expected success behavior.

## 5. Hand off secrets, then enable

1. Ask the user to fill the child's section in `config/private/mcps/<id>.json` (and replace any `<SET_BY_USER>`), listing the key names (from `keys <id>`), or to keep the accounts in the child's own files. Never ask for the values in chat.
2. After the user confirms, set `"enabled": true` for the server and the feature in `hub.json`.
3. Restart the MCP client that runs the Hub (`scripts/start.ps1` / `scripts/start.sh`, see README → Run). Config is read only at startup.

## 6. Verify

Check the newest events in `logs/mcp-hub.jsonl`:

| You see | Meaning | Fix |
|---------|---------|-----|
| `server.connected` (server `<id>`) + `hub.tools.registered` listing your tools | Working | Done |
| `hub__status` lists the child as `credentials required` (or `config.load.failed` with "unfilled `<SET_BY_USER>`" when `MCP_HUB_CONFIG` is set) | Secrets not filled | Back to step 5.1 |
| `config.load.failed` with other text | Invalid config (message names the key) | Fix it with `private_config.py set` or in `hub.json` |
| `server.connect.failed` / `server.list_tools.failed` | Child didn't start or answer within `connectTimeoutSeconds` (`error_type=TimeoutError`) | Check `cwd`, `command`, `args`, and the child's stderr in `logs/children/<server-id>.log` (the user reads it; it may contain secrets). The Hub keeps retrying (`server.reconnect.failed`). |
| `route.skipped` with `tool not found` | Wrong tool name in the allowlist | Fix `name` in `hub.json` |

Add one or more read-only checks for the child to `CHECKS` in `scripts/smoke.py`, using a tool that proves it reaches the required upstream service. The success check must validate each tool's real response shape, not only the absence of an MCP error flag. Add unit tests for successful, malformed, and error results, then run the definition-of-done commands in [AGENTS.md](../AGENTS.md). For an enabled child without an entry, `smoke.py` checks only the connection and prints `PASS  N tools, connected (no read-only check in CHECKS)`.

A new child should follow [Configuration sources](child-mcp-compatibility.md#configuration-sources): set `env.CENTRAL_CONFIG_PATH` to `../mcp-hub/config/private/mcps/<id>.json`, put its account keys in one section with empty values, and have the child fall back to its own files and report the source in its check tool. All built-in children with an upstream account work this way.

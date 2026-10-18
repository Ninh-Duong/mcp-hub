# Other MCP clients

Use this file when the client has no file of its own in this folder. Do the general procedure in [../connecting-a-client.md](../connecting-a-client.md), and map steps 6–8 as below. `<P>` = the mcp-hub repo's absolute path, with forward slashes.

## 1. MCP entry
Find the client's stdio/local server config. If you can't, *ask* the user or check the client's current MCP docs. Then map the standard entry (connecting-a-client.md §2) onto it:
- name `mcp-hub`;
- command `powershell` with args `["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "<P>/scripts/start.ps1"]`, or on macOS/Linux `bash` with `["<P>/scripts/start.sh"]`;
- no env.

Watch three variations:
- **Top-level key:** `mcpServers`, `servers`, `context_servers`, `mcp`, or `extensions`.
- **`type`:** whether it is required, and its value (`stdio` or `local`).
- **Command shape:** whether command and args are one array.

Set a startup timeout of 60–120 s if the client has one.

## 2. Always-on rule
Put the content of `<P>/docs/client-rules.md` into the client's always-on global rules or custom instructions. Use an import or reference instead of a copy if the client supports one. If the client only has workspace rules, use the user's workspace, never this repo. If the client passes MCP server instructions to the model, the rule is a backup, but install it anyway.

## 3. The `/mcp-hub` command
- If the client supports Agent Skills (`SKILL.md` folders), copy `<P>/docs/clients/templates/mcp-hub/SKILL.md` into its user skills folder (often `~/.agents/skills/mcp-hub/`).
- If it has custom slash commands in another format, port the same text, keeping the user's input as `<server-id> <task>`. For a TOML example, see `templates/mcp-hub.toml`.
- If it shows MCP prompts as commands, the Hub's per-child prompts (named after each server ID) work too.
- Otherwise the user types `mcp-hub:<server-id> <task>` as plain text.

## 4. Reload and verify
Reload as the table below says. Then check that tools named `<prefix>__<tool>` are listed, and that `mcp-hub:mcp-server-logs list the services in dev` uses only `logs__*` tools, with `env: "dev"`.

## Reference (MCP config only; checked 2026-10-17, so verify before use)

| Client | Config file (global; project) | Shape / notes | Reload |
|---|---|---|---|
| Claude Desktop | `%APPDATA%\Claude\claude_desktop_config.json` (Store build: `%LOCALAPPDATA%\Packages\Claude_<id>\LocalCache\Roaming\Claude\`); macOS `~/Library/Application Support/Claude/` | `mcpServers`, no `type` | Quit from the tray, reopen |
| Copilot CLI | `~/.copilot/mcp-config.json`; `.mcp.json` | `mcpServers`, `"type": "local"`, `"tools": ["*"]` | `copilot mcp list` |
| Cline | via Cline pane → MCP Servers → Configure | `mcpServers` + **`"type": "stdio"`** (otherwise SSE) | Restart button |
| Roo Code | global `mcp_settings.json`; `.roo/mcp.json` | `mcpServers`; optional `timeout` (s) | Toggle in MCP view |
| Continue | `~/.continue/config.yaml`; `.continue/mcpServers/mcp-hub.yaml` | YAML list `mcpServers: - name: mcp-hub, type: stdio, command, args`; Agent mode only | Save reloads |
| Zed | `~/.config/zed/settings.json` (Windows `%APPDATA%\Zed\settings.json`); `.zed/settings.json` | top key **`context_servers`** | Automatic |
| JetBrains AI / Junie | UI: Settings → Tools → AI Assistant → MCP; Junie `~/.junie/mcp/mcp.json` | paste entry (STDIO) / `mcpServers` | Reconnect |
| Kiro | `~/.kiro/settings/mcp.json`; `.kiro/settings/mcp.json` | `mcpServers` | Save reconnects |
| OpenCode | `~/.config/opencode/opencode.json`; `opencode.json` | top key `mcp`, `"type": "local"`, **`command` is one array** (`["powershell", "-NoProfile", …]`), `"timeout": 120000` | Restart |
| Goose | `~/.config/goose/config.yaml` (Windows `%APPDATA%\Block\goose\config\config.yaml`) | `extensions: mcp-hub: {type: stdio, cmd: powershell, args: [...], enabled: true, timeout: 300}` | New session |

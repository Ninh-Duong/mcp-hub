# Cursor (editor and `agent` CLI)

Do the general procedure in [../connecting-a-client.md](../connecting-a-client.md), and use this file for steps 6–8. `<P>` = the mcp-hub repo's absolute path, with forward slashes. Facts were checked against the official docs on 2026-10-19.

## 1. MCP entry
- **File:** `~/.cursor/mcp.json` (global). A project file `.cursor/mcp.json` in the user's workspace overrides it when the name is the same.
- **Shape:** top-level `mcpServers`, with `"type": "stdio"`:
  ```json
  { "mcpServers": { "mcp-hub": { "type": "stdio",
      "command": "powershell",
      "args": ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "<P>/scripts/start.ps1"] } } }
  ```
  macOS/Linux: `"command": "bash", "args": ["<P>/scripts/start.sh"]`.

## 2. Always-on rule
- The docs do not say whether Cursor passes MCP server instructions to the model, so the rule is required.
- **User Rules (every workspace):** Customize → Rules → User Rules. This is UI-only. Ask the user to paste the content of `<P>/docs/client-rules.md` there; you cannot write it yourself.
- **Per workspace (a file you can write):** `.cursor/rules/mcp-hub.mdc` in the user's workspace, never this repo. Plain `.md` files there are ignored. Start it with:
  ```markdown
  ---
  description: Rules for using the mcp-hub MCP server
  alwaysApply: true
  ---
  ```
  Then add the content of `client-rules.md`.

## 3. The `/mcp-hub` command
- **Install:** copy `<P>/docs/clients/templates/mcp-hub/SKILL.md` to `~/.agents/skills/mcp-hub/SKILL.md`. That gives `/mcp-hub`.
- Cursor also reads `~/.cursor/skills` and `~/.claude/skills`. Install only one copy, to avoid duplicate commands.
- MCP prompts are listed as supported, but their command format is not documented. Use the skill.

## 4. Reload and verify
1. Save, then restart Cursor. Check that `mcp-hub` is enabled under Customize → MCPs. CLI: `agent mcp list` and `agent mcp list-tools mcp-hub`.
2. `/mcp-hub mcp-server-logs list the services in dev` (Agent chat) uses only `logs__*` tools, with `env: "dev"`.

## 5. Daily use
- `/mcp-hub <server-id> <task>`, or `mcp-hub:<server-id> <task>` as plain text.

## 6. Logs and quirks
- Output panel (Ctrl+Shift+U) → **MCP Logs**.
- User Rules apply to Agent chat only, not to Inline Edit.
- If a server misbehaves after a config change, remove it and add it again.

Sources: [MCP](https://cursor.com/docs/mcp), [MCP help](https://cursor.com/help/customization/mcp), [CLI MCP](https://cursor.com/docs/cli/mcp), [Rules](https://cursor.com/docs/context/rules), [Skills](https://cursor.com/docs/context/skills).

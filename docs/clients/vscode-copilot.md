# GitHub Copilot in VS Code (agent mode)

Do the general procedure in [../connecting-a-client.md](../connecting-a-client.md), and use this file for steps 6–8. `<P>` = the mcp-hub repo's absolute path, with forward slashes. Facts were checked against the official docs on 2026-10-28.

## 1. MCP entry
- For the shared Windows service, this guide uses the **stdio bridge** because the Copilot harness's HTTP config has no built-in helper that can read the Hub token from private storage. Keep the local command and append `-Bridge` to its args:
  ```json
  { "servers": { "mcp-hub": { "type": "stdio",
      "command": "powershell",
      "args": ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "<P>/scripts/start.ps1", "-Bridge"] } } }
  ```
  The existing entry without `-Bridge` below starts an isolated per-client Hub.
- **File:** the user-profile `mcp.json` (Command Palette → **MCP: Open User Configuration**). A workspace-only alternative is `.vscode/mcp.json` in the user's workspace.
- **Shape:** the top-level key is **`servers`** (not `mcpServers`), with `"type": "stdio"`:
  ```json
  { "servers": { "mcp-hub": { "type": "stdio",
      "command": "powershell",
      "args": ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "<P>/scripts/start.ps1"] } } }
  ```
  macOS/Linux: `"command": "bash", "args": ["<P>/scripts/start.sh"]`.
- Do not also add it to `~/.copilot/mcp-config.json`, or the server appears twice.

## 2. Always-on rule
- VS Code includes MCP server instructions in its base prompt (since 1.104). Install the rule as well, for consistency.
- **File:** append the content of `<P>/docs/client-rules.md` to `~/.copilot/copilot-instructions.md`, the personal always-on instructions. This needs the setting `github.copilot.chat.codeGeneration.useInstructionFiles` to be on.

## 3. The `/mcp-hub` command
- **Install:** copy `<P>/docs/clients/templates/mcp-hub/SKILL.md` to `~/.agents/skills/mcp-hub/SKILL.md`. That gives `/mcp-hub` in Copilot Chat.
- VS Code also shows the Hub's MCP prompts natively, as `/mcp-hub.<server-id>` (older builds: `/mcp.mcp-hub.<server-id>`), and asks for `task` in a dialog.

## 4. Reload and verify
1. Command Palette → **MCP: List Servers** → `mcp-hub` → Start (or Restart). Accept the trust prompt. Use Chat in **Agent** mode.
2. `/mcp-hub mcp-server-logs list the services in dev` uses only `logs__*` tools, with `env: "dev"`.

## 5. Daily use
- Call `hub__status` to see the automatically detected sibling MCPs and readiness before choosing a child.
- `/mcp-hub <server-id> <task>`, `/mcp-hub.<server-id>`, or `mcp-hub:<server-id> <task>` as plain text.

## 6. Logs and quirks
- **MCP: List Servers** → `mcp-hub` → **Show Output**. For more detail, run **Developer: Set Log Level** → Trace. The Chat Debug view shows what the model received.
- The limit is 128 tools per request. Check the currently allowlisted Hub tools if the child set changes.

Sources: [MCP servers](https://code.visualstudio.com/docs/copilot/customization/mcp-servers), [MCP config reference](https://code.visualstudio.com/docs/agents/reference/mcp-configuration), [Custom instructions](https://code.visualstudio.com/docs/copilot/customization/custom-instructions), [Agent skills](https://code.visualstudio.com/docs/copilot/customization/agent-skills), [Release 1.104](https://code.visualstudio.com/updates/v1_104), [Troubleshooting](https://code.visualstudio.com/docs/agents/agent-troubleshooting/troubleshooting).

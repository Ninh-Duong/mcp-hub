# Google Antigravity (App, IDE, `agy` CLI)

Do the general procedure in [../connecting-a-client.md](../connecting-a-client.md), and use this file for steps 6–8. `<P>` = the mcp-hub repo's absolute path, with forward slashes. Facts were checked against the official docs on 2026-10-19.

## 1. MCP entry
- **File:** `~/.gemini/config/mcp_config.json`, shared by the App, IDE, and CLI. Some builds also keep `~/.gemini/antigravity/mcp_config.json`; if it exists, apply the same change there. A workspace-only alternative is `.agents/mcp_config.json` in the user's workspace, never this repo.
- **Shape:** top-level `mcpServers`, with no `type` field. `cwd` and `env` exist; there is no startup-timeout field.
  ```json
  { "mcpServers": { "mcp-hub": {
      "command": "powershell",
      "args": ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "<P>/scripts/start.ps1"] } } }
  ```
  macOS/Linux: `"command": "bash", "args": ["<P>/scripts/start.sh"]`.
- There is no CLI `add` command. Edit the file.

## 2. Always-on rule
- **File:** `~/.gemini/config/rules/mcp-hub.md`. It must start with this frontmatter, or Antigravity drops the file silently:
  ```markdown
  ---
  trigger: always_on
  ---
  ```
  After the frontmatter, add the full content of `<P>/docs/client-rules.md`.
- **Limits:** 24,000 bytes per file; global plus `always_on` rules share a 20,000-token budget.
- Do not use `~/.gemini/GEMINI.md` for this rule. Gemini CLI loads that file too, so the rules would be duplicated.
- The rule is required here: the docs do not say whether Antigravity passes MCP server instructions to the model.

## 3. The `/mcp-hub` command
- In Antigravity, `/` commands come from **skills**. An MCP server name never becomes a command, and MCP prompts are not documented as commands. Without a skill, `/mcp-hub` shows "No matching results".
- **Install:** first read `~/.gemini/config/skills.json`.
  - If its `entries` list `~/.agents/skills`, copy `<P>/docs/clients/templates/mcp-hub/SKILL.md` to `~/.agents/skills/mcp-hub/SKILL.md`. Codex, Cursor, and Copilot read that folder too.
  - Otherwise use the documented global folder: `~/.gemini/config/skills/mcp-hub/SKILL.md` (CLI: `~/.gemini/antigravity-cli/skills/mcp-hub/SKILL.md`).
  - Keep only one copy.
- Do not create a workflow; workflows are deprecated (checked 2026-10-19).

## 4. Reload and verify
1. Reload:
   - **App:** Settings → Customizations → Installed MCP Servers → **Refresh**.
   - **IDE:** Agent panel → `…` → MCP Servers → Manage → **Refresh**.
   - **CLI:** `/mcp`.
2. `mcp-hub` is listed with the Hub's tools (`jira__*`, `logs__*`). A cache folder `~/.gemini/antigravity/mcp/mcp-hub/` appears after the first successful load (observed behavior).
3. Typing `/` lists `mcp-hub`.
4. `/mcp-hub mcp-server-logs list the services in dev` uses only `logs__*` tools, with `env: "dev"`.

## 5. Daily use
- `/mcp-hub <server-id> <task>`, e.g. `/mcp-hub mcp-server-logs show the prod logs for service orders-api`.
- Without the skill, type `mcp-hub:<server-id> <task>` (no leading `/`; the `/` opens the command menu).

## 6. Logs and quirks
- The CLI's `/mcp` manager shows live connection logs. No log file path is documented.
- `/mcp-hub` gives "No matching results" when the skill is not installed, or the window wasn't reloaded after installing it.
- Tools are missing when the Hub hasn't been refreshed, or a child failed. Run `uv run python scripts/smoke.py` in `<P>`.

Sources: [MCP](https://antigravity.google/docs/mcp), [Rules](https://antigravity.google/docs/rules), [Skills](https://antigravity.google/docs/skills), [Slash commands](https://antigravity.google/docs/slash-commands), [Workflows](https://antigravity.google/docs/ide/workflows).

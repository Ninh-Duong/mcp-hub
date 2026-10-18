# Claude Code (CLI, VS Code and JetBrains extensions)

Do the general procedure in [../connecting-a-client.md](../connecting-a-client.md), and use this file for steps 6–8. `<P>` = the mcp-hub repo's absolute path, with forward slashes. Facts were checked against the official docs on 2026-10-19.

## 1. MCP entry
- **Command (user scope, all projects):**
  ```bash
  claude mcp add --transport stdio --scope user mcp-hub -- powershell -NoProfile -ExecutionPolicy Bypass -File "<P>/scripts/start.ps1"
  ```
  macOS/Linux: `… mcp-hub -- bash "<P>/scripts/start.sh"`.
- Everything after `--` is passed to the server unchanged. Keep the name before `--`; a name placed right after `--env` would be read as `KEY=value`.
- **Storage:** user and local scope live in `~/.claude.json`. Project scope is `.mcp.json` in the user's workspace; it needs a one-time approval there.
- **Startup timeout:** set the env var `MCP_TIMEOUT` (ms), or add `"timeout": 120000` to the entry.

## 2. Always-on rule
- Claude Code loads MCP server instructions at session start, cut to 4,096 characters. The Hub puts its essentials first, but install the full rule as well.
- **File:** add one import line to `~/.claude/CLAUDE.md`. Escape spaces with `\`, because quoted paths are not imported:
  ```text
  @<P>/docs/client-rules.md
  ```
  For example, a repo under `…/My Repos/mcp-hub` becomes `@…/My\ Repos/mcp-hub/docs/client-rules.md`. Imports in user-level files load without an approval prompt. The import always reads the current file, so it never goes stale.

## 3. The `/mcp-hub` command
- **Install:** copy `<P>/docs/clients/templates/mcp-hub/SKILL.md` to `~/.claude/skills/mcp-hub/SKILL.md`. That gives `/mcp-hub <server-id> <task>`, with the input substituted into `$ARGUMENTS`.
- Claude Code also shows the Hub's MCP prompts natively as `/mcp-hub:<server-id> (MCP)`, e.g. `/mcp-hub:mcp-server-logs`. Prompt arguments are split on spaces, though, so a multi-word task gets cut. Prefer the skill.

## 4. Reload and verify
1. Check with `claude mcp list` and `claude mcp get mcp-hub`. In a session, use `/mcp` → Reconnect, or `/mcp reconnect all`.
2. Start a new session if `/mcp-hub` is not in the `/` menu yet.
3. `/mcp-hub mcp-server-logs list the services in dev` uses only `logs__*` tools, with `env: "dev"`.

## 5. Daily use
- `/mcp-hub <server-id> <task>` (skill).
- `/mcp-hub:<server-id> <one-word-task>` (native MCP prompt).
- `mcp-hub:<server-id> <task>` (plain text).

## 6. Logs and quirks
- `claude --debug=mcp` writes server stderr to `~/.claude/debug/<session-id>.txt`.
- Characters outside `A-Za-z0-9_-` in server names become `_` in prompt commands.

Sources: [MCP](https://code.claude.com/docs/en/mcp), [Memory / CLAUDE.md](https://code.claude.com/docs/en/memory), [Skills](https://code.claude.com/docs/en/skills), [Debug your config](https://code.claude.com/docs/en/debug-your-config).

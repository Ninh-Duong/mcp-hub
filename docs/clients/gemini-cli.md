# Gemini CLI

Do the general procedure in [../connecting-a-client.md](../connecting-a-client.md), and use this file for steps 6–8. `<P>` = the mcp-hub repo's absolute path, with forward slashes. Facts were checked against the official docs on 2026-10-19. Since 2026-06-18, Gemini CLI has been replaced by the Antigravity CLI for unpaid-tier and Google One users; if that applies to the user, use [antigravity.md](antigravity.md).

## 1. MCP entry
- **File:** `~/.gemini/settings.json` (global) or `.gemini/settings.json` in the user's workspace. The top-level key is `mcpServers`, with no `type`; a `command` key means stdio:
  ```json
  { "mcpServers": { "mcp-hub": {
      "command": "powershell",
      "args": ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "<P>/scripts/start.ps1"] } } }
  ```
  macOS/Linux: `"command": "bash", "args": ["<P>/scripts/start.sh"]`.
- `gemini mcp add -s user mcp-hub <command> [args...]` also works. Editing the file is safer when the args start with `-`.
- `timeout` there is a per-request timeout (default 600000 ms), not a startup timeout.

## 2. Always-on rule
- Not needed. Gemini CLI appends MCP server instructions to its system instructions, and the Hub's instructions include the rules.
- Do not add the rule to `~/.gemini/GEMINI.md`. Antigravity also reads that file, so the rules would be duplicated there.

## 3. The `/mcp-hub` command
- **Install:** copy `<P>/docs/clients/templates/mcp-hub.toml` to `~/.gemini/commands/mcp-hub.toml`. That gives `/mcp-hub <server-id> <task>`, with the input substituted into `{{args}}`.
- Gemini CLI also shows the Hub's MCP prompts natively as `/<server-id>`, e.g. `/mcp-server-logs --task="list services in dev"`.
- Skills (`~/.agents/skills`) are triggered by the model here, not by `/`. The TOML command is what gives you `/mcp-hub`.

## 4. Reload and verify
1. `/mcp reload`, then `/commands reload`. Check with `gemini mcp list` or `/mcp list`.
2. `/mcp-hub mcp-server-logs list the services in dev` uses only `logs__*` tools, with `env: "dev"`.

## 5. Daily use
- `/mcp-hub <server-id> <task>`, `/<server-id> --task="…"`, or `mcp-hub:<server-id> <task>` as plain text.

## 6. Logs and quirks
- Run `gemini --debug`, or press F12 for the debug console; it captures server stderr. `/mcp list` shows connection errors.
- Stdio servers connect only in trusted folders.

Sources: [MCP servers](https://geminicli.com/docs/tools/mcp-server/), [Commands](https://geminicli.com/docs/reference/commands), [GEMINI.md](https://geminicli.com/docs/cli/gemini-md/), [Custom commands](https://geminicli.com/docs/cli/custom-commands/), [Skills](https://geminicli.com/docs/cli/skills/).

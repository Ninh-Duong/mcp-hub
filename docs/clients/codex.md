# OpenAI Codex (CLI, IDE extension, app)

Do the general procedure in [../connecting-a-client.md](../connecting-a-client.md), and use this file for steps 6–8. `<P>` = the mcp-hub repo's absolute path, with forward slashes. Facts were checked against the official docs on 2026-10-28. The CLI, IDE extension, and app share one config.

## 1. MCP entry
- **Shared Windows service (HTTP):** after `scripts/hubctl.ps1 install`, configure this in `~/.codex/config.toml`. Codex supports a local HTTP header helper, so the bearer token stays in the Hub's private service file and is read at connection time:
  ```toml
  [mcp_servers.mcp-hub]
  url = "http://127.0.0.1:8765/mcp"
  http_headers_helper = 'powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "<P>/scripts/http_headers.ps1"'
  startup_timeout_sec = 120
  ```
  If the installed Codex version does not support `http_headers_helper`, use the stdio bridge below.
- **Shared stdio bridge:** configure the existing command with `-Bridge` appended to args: `['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', '<P>/scripts/start.ps1', '-Bridge']`. It connects to the shared service instead of starting another child set.
- **Legacy per-client stdio:** the existing `codex mcp add mcp-hub -- powershell … -File "<P>/scripts/start.ps1"` entry below remains available when an isolated Hub is wanted.
- **Command:**
  ```bash
  codex mcp add mcp-hub -- powershell -NoProfile -ExecutionPolicy Bypass -File "<P>/scripts/start.ps1"
  ```
  macOS/Linux: `… mcp-hub -- bash "<P>/scripts/start.sh"`.
- Then raise the startup timeout in `~/.codex/config.toml`. The default is 10 s, which is too short for the Hub. Use single-quoted TOML strings for Windows paths:
  ```toml
  [mcp_servers.mcp-hub]
  command = "powershell"
  args = ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', '<P>/scripts/start.ps1']
  startup_timeout_sec = 120
  ```
- A project-only alternative is `.codex/config.toml` in a trusted workspace of the user, never this repo.

## 2. Always-on rule
- Codex reads the Hub's server instructions. The Hub keeps their first 512 characters self-contained, as the Codex docs advise.
- **File:** append the content of `<P>/docs/client-rules.md` to `~/.codex/AGENTS.md`, under a `# mcp-hub` heading. If `~/.codex/AGENTS.override.md` exists, it replaces `AGENTS.md`, so append there instead.
- There is no import syntax. When `client-rules.md` changes, re-copy it. The combined limit is 32 KiB (`project_doc_max_bytes`).

## 3. The `/mcp-hub` command
- **Install:** copy `<P>/docs/clients/templates/mcp-hub/SKILL.md` to `~/.agents/skills/mcp-hub/SKILL.md`.
- **Invoke:** `$mcp-hub <server-id> <task>`, or pick it from `/skills`. Codex has no formal skill arguments; the skill reads the text after the name.
- Codex does not support MCP prompts, so the Hub's per-child prompts are not available here. The skill reads the child's contract instead. Custom prompts (`~/.codex/prompts`) are deprecated; don't use them.

## 4. Reload and verify
1. Check with `codex mcp list`, or `/mcp` in the TUI or app. In the IDE: gear → MCP servers.
2. Start a new session so the skill and `AGENTS.md` load.
3. `$mcp-hub mcp-server-logs list the services in dev` uses only `logs__*` tools, with `env: "dev"`.

## 5. Daily use
- Call `hub__status` to see the automatically detected sibling MCPs and readiness before choosing a child.
- `$mcp-hub <server-id> <task>`, or `mcp-hub:<server-id> <task>` as plain text.

## 6. Logs and quirks
- `RUST_LOG=debug codex -c log_dir=./.codex-log` writes `./.codex-log/codex-tui.log`. Session transcripts are in `~/.codex/sessions`.
- A first-start timeout means `startup_timeout_sec` is missing or too low. Also run `uv run python scripts/smoke.py` in `<P>` first.

Sources: [MCP](https://learn.chatgpt.com/docs/extend/mcp?surface=cli), [Codex configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference), [AGENTS.md](https://learn.chatgpt.com/docs/agent-configuration/agents-md), [Skills](https://learn.chatgpt.com/docs/build-skills), [Custom prompts (deprecated)](https://learn.chatgpt.com/docs/custom-prompts), [MCP prompts request #5059](https://github.com/openai/codex/issues/5059).

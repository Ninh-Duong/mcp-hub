# Connecting an AI client to mcp-hub

> **You are an AI agent, and the user wants their client (Antigravity, Claude Code, Cursor, Copilot, Codex, …) to use mcp-hub.** Follow this file from top to bottom. Ask the user only where a step says *Ask*. This task changes **the client's** MCP config. It does not change anything in this repository.

---

## 0. For the human: the prompts

Paste one of these into any agent. Replace `<MCP_HUB_PATH>` with this repo's absolute path (forward slashes). The agent will ask before it writes global config or removes entries.

**A. Full setup (connect + install the rule).** Use this the first time on each client:

```text
Read <MCP_HUB_PATH>/docs/connecting-a-client.md and follow every step to connect yourself to mcp-hub:
register one MCP server named mcp-hub, replace any old entries for its child MCPs, and install
<MCP_HUB_PATH>/docs/client-rules.md as an always-on global rule (step 7). Ask me before writing
global config or removing entries, and report what you changed at the end.
```

The user may write these prompts in any language. Agents: follow this file the same way regardless.

**B. Instructions only, no changes:**

```text
Read <MCP_HUB_PATH>/docs/connecting-a-client.md and tell me exactly what to change in this client
to use mcp-hub, including the rule from step 7. Do not edit any file.
```

In this mode the agent does only steps 1, 2, and 3, plus step 5 as a list: it names the old entries and removes none. It gives the entry, the rule file, and the commands for steps 4, 6, 7 and 8 instead of running them.

**C. Already connected; only install or refresh the rule** (e.g. after `client-rules.md` changed):

```text
Install <MCP_HUB_PATH>/docs/client-rules.md as an always-on global rule in this client, following
step 7 of <MCP_HUB_PATH>/docs/connecting-a-client.md. Replace the old copy if there is one.
```

**D. Daily use:** `mcp-hub:<server-id> <task>`, e.g. `mcp-hub:mcp-server-logs show the prod logs for service orders-api` or `mcp-hub:jira-sync-ticket check connection`. The rules are in [client-rules.md](client-rules.md).

---

## 1. Rules for the agent

- **Do not edit this repository.** Do not edit `config/`, `scripts/`, or the docs, and never read `config/private/mcps/`, `.local/`, or `.env*`. Adding or changing a child MCP is a different task: [adding-a-child-mcp.md](adding-a-child-mcp.md).
- **Absolute paths go only into the client's config file**, never into a file tracked in this repo. Use forward slashes in JSON: `<MCP_HUB_PATH>/scripts/start.ps1`. Do not use `~`, `%USERPROFILE%`, or `$HOME` inside `args`.
- **Merge, don't overwrite.** Client configs usually hold other servers. Back the file up as `<file>.mcp-hub.bak` (never overwrite an existing `.bak`), add or replace only the entries named here, and keep the file valid JSON/TOML/YAML.
- **"Project" config means the user's own workspace**, never this repo.
- **Ask** before writing a global/user config, and before removing an existing entry.
- Never print, copy, or ask for credentials. The Hub's clients need none; each child MCP keeps its own.

---

## 2. The server entry (every client maps to this)

| Field | Windows | macOS / Linux |
|---|---|---|
| name | `mcp-hub` | `mcp-hub` |
| transport | stdio | stdio |
| command | `powershell` | `bash` |
| args | `["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "<MCP_HUB_PATH>/scripts/start.ps1"]` | `["<MCP_HUB_PATH>/scripts/start.sh"]` |
| env | none | none |

```json
{
  "mcpServers": {
    "mcp-hub": {
      "command": "powershell",
      "args": ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "<MCP_HUB_PATH>/scripts/start.ps1"]
    }
  }
}
```

The launcher finds `uv` (on `PATH` or in the repo's `.tools/`), checks Python, and starts the Hub. When a client starts it (non-interactive), it never writes to stdout, which is reserved for MCP traffic. This entry has been tested end to end as a stdio server, including non-ASCII tool output.

**Fallback** if the client cannot run PowerShell/bash: `command` = the absolute path of `uv` (`where uv` / `which uv`, or `<MCP_HUB_PATH>/.tools/uv.exe`), `args` = `["--directory", "<MCP_HUB_PATH>", "run", "--locked", "mcp-hub"]`.

---

## 3. Steps

Each step ends with **Done when**. If a step cannot be done, stop and tell the user.

### Step 1: Identify the client and OS
Find out which client you are running in (your system prompt or product name usually says). If unsure, *ask*. Detect the OS and shell.
**Done when** you have picked one row of §4.

### Step 2: Find the repo
`<MCP_HUB_PATH>` is the folder that contains `scripts/start.ps1` and this file. Record it as an absolute path with forward slashes.
**Done when** `<MCP_HUB_PATH>/scripts/start.ps1` exists.

### Step 3: Prerequisites
Check `uv --version`, or check whether `<MCP_HUB_PATH>/.tools/uv.exe` (`.tools/uv` on macOS/Linux) exists. If neither works, *ask* the user to run the launcher once in a terminal, because the client cannot answer its install prompt:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "<MCP_HUB_PATH>/scripts/start.ps1"
```

On macOS/Linux: `bash "<MCP_HUB_PATH>/scripts/start.sh"`.

It asks before installing `uv` / Python 3.12. When it then sits silently, the Hub is running: press Ctrl+C.
**Done when** `uv` is found.

### Step 4: Check the Hub and its children
In `<MCP_HUB_PATH>`, run (use the `.tools` uv path if `uv` is not on `PATH`):

```powershell
uv run python scripts/smoke.py
```

Expect one `PASS` line per child MCP. A `FAIL` line names the cause; report it to the user and stop. Fixing children belongs to [adding-a-child-mcp.md](adding-a-child-mcp.md). This run also finishes any first-time install or build, so the client won't time out on its first start.
**Done when** every line is `PASS`.

### Step 5: Find old direct entries (migration)
The Hub replaces direct connections to its children. Each child is described in `docs/mcps/<server-id>.md` (skip `README.md`, which is the template), and its `Repository:` line names the child repo (e.g. `../mcp-server-logs`, `../jira_sync_ticket`). In the client's config, find entries that either:
- have `command`/`args` pointing into one of those repos (e.g. `…/mcp-server-logs/start.mjs`, `…/jira_sync_ticket/server.py`), or
- are named after an old name of a child (`mcp-logs`, `log-hub*`, `mcp-server-log-hub`).

Never remove an entry just because its name mentions a product (e.g. another Jira or Atlassian server). It must point into a child repo or carry one of the old names above.

*Ask* the user, then remove them. Keeping both exposes every tool twice under different names.

If a removed entry set `env` values (e.g. `OPENOBSERVE_ENV`), tell the user. The Hub entry carries no env, so that setting now belongs in the child's Hub config. Setting it is a separate task in [adding-a-child-mcp.md](adding-a-child-mcp.md), e.g. `uv run python scripts/private_config.py set mcp-server-logs env.OPENOBSERVE_ENV '"prod"'`.

If the client has the config in two places (e.g. Antigravity's current and older-build paths), apply the same change to every file that exists. Leftover per-server cache folders from the old entries can be left alone.
**Done when** no entry other than `mcp-hub` points at a child repo.

### Step 6: Register `mcp-hub`
Add the §2 entry using the client's row in §4. Prefer the client's CLI command when it has one. Raise the startup timeout to 60–120 s where the client allows it, because the Hub starts every child before it answers.
**Done when** the entry is saved and the file still parses.

### Step 7: Install the client rule
[client-rules.md](client-rules.md) tells the agent how to use the Hub, e.g. `mcp-hub:<server-id> <task>`, the `env` rules, and how to treat writing tools. The Hub already sends it as server instructions, but some clients don't show those to the model. Install it as an always-on rule too, at user/global level so it works in every workspace. *Ask* before writing.

| Client | Where | How |
|---|---|---|
| Google Antigravity | `~/.gemini/config/rules/mcp-hub.md` | New file: a frontmatter block `---` / `trigger: always_on` / `---`, then the content of `client-rules.md`. |
| Claude Code | `~/.claude/CLAUDE.md` | Append one import line: `@<MCP_HUB_PATH>/docs/client-rules.md`. It stays current automatically. |
| OpenAI Codex | `~/.codex/AGENTS.md` | Append the content under a `# mcp-hub` heading. |
| Cursor | Settings → Rules → User Rules | Paste the content. |
| VS Code + Copilot | the user's custom instructions, or the workspace's `.github/copilot-instructions.md` | Append the content. |
| Any other | the client's always-on global rules or custom instructions | Copy the content. If the client has only workspace rules, use the user's workspace, never this repo. |

A copy does not update itself. When `client-rules.md` changes, copy it again. The import, and the Hub's server instructions, are always current.
**Done when** the rule file is in place, or the user declined.

### Step 8: Reload and verify
1. Reload as the client's row says. If the user must restart the app, tell them exactly what to do and continue after they confirm.
2. The client should list tools named `<prefix>__<tool>` (e.g. `jira__check_connection`, `logs__query_logs`). If it shows MCP prompts, there is one per child, named after its server ID.
3. For each child, call its read-only check tool from the `CHECKS` table in `scripts/smoke.py` (today `jira__check_connection` and `logs__list_openobserve_organizations`).
4. Test the call style: `mcp-hub:mcp-server-logs list the services in dev`. The agent should use only `logs__*` tools with `env: "dev"`.
5. Report to the user: the config file you changed, the entries you removed, where the rule was installed, and the tool calls that worked.

**Done when** the read-only calls succeed.

---

## 4. Client reference

`<P>` = `<MCP_HUB_PATH>`, and "entry" = §2. Paths were checked against the clients' docs on 2026-10-17. If a path does not exist, look up the client's current MCP docs and map §2 onto it. Rows show the Windows command. On macOS/Linux, use `bash` with `["<P>/scripts/start.sh"]` instead (e.g. `claude mcp add … mcp-hub -- bash "<P>/scripts/start.sh"`).

| Client | Config file (global; project) | Shape / notes | Reload |
|---|---|---|---|
| Google Antigravity | `~/.gemini/config/mcp_config.json` (older builds: `~/.gemini/antigravity/mcp_config.json`); `.agents/mcp_config.json` | `mcpServers`, entry as-is. UI: Agent panel → `…` → MCP Servers → Manage → View raw config | **Refresh** in the MCP panel; CLI `/mcp` |
| Claude Code | `~/.claude.json` (user/local); `.mcp.json` | `claude mcp add --transport stdio --scope user mcp-hub -- powershell -NoProfile -ExecutionPolicy Bypass -File "<P>/scripts/start.ps1"`. File shape adds `"type": "stdio"` | `/mcp`, `claude mcp list` |
| Claude Desktop | `%APPDATA%\Claude\claude_desktop_config.json` (Store build: `%LOCALAPPDATA%\Packages\Claude_<id>\LocalCache\Roaming\Claude\`); macOS `~/Library/Application Support/Claude/` | `mcpServers`, no `type` | Quit from the tray, reopen |
| Cursor | `~/.cursor/mcp.json`; `.cursor/mcp.json` | `mcpServers` + `"type": "stdio"` | Settings → MCP toggle |
| VS Code + Copilot | user `mcp.json` (*MCP: Open User Configuration*); `.vscode/mcp.json` | top key **`servers`** + `"type": "stdio"` | *MCP: List Servers* → Restart |
| Copilot CLI | `~/.copilot/mcp-config.json`; `.mcp.json` | `mcpServers`, `"type": "local"`, `"tools": ["*"]` | `copilot mcp list` |
| Cline | via Cline pane → MCP Servers → Configure | `mcpServers` + **`"type": "stdio"`** (otherwise SSE) | Restart button |
| Roo Code | global `mcp_settings.json`; `.roo/mcp.json` | `mcpServers`; optional `timeout` (s) | Toggle in MCP view |
| Continue | `~/.continue/config.yaml`; `.continue/mcpServers/mcp-hub.yaml` | YAML list `mcpServers: - name: mcp-hub, type: stdio, command, args`; Agent mode only | Save reloads |
| Gemini CLI | `~/.gemini/settings.json`; `.gemini/settings.json` | `mcpServers` | `/mcp` |
| OpenAI Codex | `~/.codex/config.toml`; `.codex/config.toml` | `[mcp_servers.mcp-hub]` `command = "powershell"`, `args = [...]` with single-quoted paths, **`startup_timeout_sec = 120`** (default 10 s is too short) | `codex mcp list`, `/mcp` |
| Zed | `~/.config/zed/settings.json` (Windows `%APPDATA%\Zed\settings.json`); `.zed/settings.json` | top key **`context_servers`** | Automatic |
| JetBrains AI / Junie | UI: Settings → Tools → AI Assistant → MCP; Junie `~/.junie/mcp/mcp.json` | paste entry (STDIO) / `mcpServers` | Reconnect |
| Kiro | `~/.kiro/settings/mcp.json`; `.kiro/settings/mcp.json` | `mcpServers` | Save reconnects |
| OpenCode | `~/.config/opencode/opencode.json`; `opencode.json` | top key `mcp`, `"type": "local"`, **`command` is one array** (`["powershell", "-NoProfile", …]`), `"timeout": 120000` | Restart |
| Goose | `~/.config/goose/config.yaml` (Windows `%APPDATA%\Block\goose\config\config.yaml`) | `extensions: mcp-hub: {type: stdio, cmd: powershell, args: [...], enabled: true, timeout: 300}` | New session |
| Any other | its stdio/local server config | Map §2. Watch the top key (`mcpServers` / `servers` / `context_servers` / `mcp` / `extensions`), whether `type` is required, and whether command and args are one array | — |

---

## 5. After connecting

- **Tool names change.** Hub tools are `<prefix>__<child tool>`. The mapping for each child is in `docs/mcps/<server-id>.md`. Rules or prompts in other repos that use the old names (e.g. `query_logs`, `jira_check_connection`) should be updated to `logs__query_logs`, `jira__check_connection`.
- **Prompts:** each child has a Hub prompt named after its server ID (e.g. `mcp-server-logs`). It lists that child's tools and its usage rules. Start there when working with one child.
- **Child prompts are not forwarded**, e.g. `investigate_api_bug` from mcp-server-logs. Use the Hub prompt instead.
- **Config is read at startup.** After the Hub's `hub.json` changes (a child added or removed), restart the `mcp-hub` server in the client. The client entry itself never changes.

---

## 6. Troubleshooting

| Symptom | Fix |
|---|---|
| `mcp-hub` fails to start / no tools | Run `uv run python scripts/smoke.py` in `<P>`. Its FAIL line names the cause. Details are in `<P>/logs/mcp-hub.jsonl`. |
| `Missing uv, Python 3.12…` (start.ps1) or `Missing prerequisites. Run bash scripts/start.sh…` (start.sh) in the client log | Prerequisites missing: have the user run step 3 in a terminal. If `uv` works in a terminal, the client starts with a different `PATH`; use the §2 fallback with the absolute `uv` path. |
| `spawn powershell ENOENT` | Use `pwsh`, or the absolute path printed by `where powershell`, or the §2 fallback. |
| Timeout on start | Run step 4 first (it finishes builds), then raise the client's startup timeout (§4). |
| A child's tools are missing | That child failed or a tool name is wrong. `smoke.py` shows which (`not connected` / `tool not found`). |
| Every tool appears twice | An old direct entry is still configured. Redo step 5. |
| Server not listed | Wrong file or top key (§4), invalid JSON, or client not reloaded. Restore the `.bak` if needed. |

# Connecting an AI client to mcp-hub

> **You are an AI agent, and the user wants their client (Antigravity, Claude Code, Cursor, Copilot, Codex, …) to use mcp-hub.** Follow this file from top to bottom. For the client-specific parts (steps 6–8), use your client's file in [clients/](clients/) (§4). Ask the user only where a step says *Ask*. This task changes **the client's** config. It does not change anything in this repository.

---

## 0. For the human: the prompts

Paste one of these into any agent. Replace `<MCP_HUB_PATH>` with this repo's absolute path (forward slashes). The agent will ask before it writes global config or removes entries.

**A. Full setup (connect, rule, `/mcp-hub` command).** Use this the first time on each client:

```text
Read <MCP_HUB_PATH>/docs/connecting-a-client.md and follow every step to connect yourself to mcp-hub,
using the file for this client in <MCP_HUB_PATH>/docs/clients/: register one MCP server named mcp-hub,
replace any old entries for its child MCPs, install the always-on rule and the /mcp-hub command.
Ask me before writing global config or removing entries, and report what you changed at the end.
```

The user may write these prompts in any language. Agents: follow this file the same way regardless.

**B. Instructions only, no changes:**

```text
Read <MCP_HUB_PATH>/docs/connecting-a-client.md and the file for this client in <MCP_HUB_PATH>/docs/clients/,
and tell me exactly what to change in this client to use mcp-hub, including the rule and the /mcp-hub
command. Do not edit any file.
```

In this mode the agent does steps 1 and 2. In step 3 it only checks, and reports the launcher command instead of asking the user to run it. Step 5 becomes a list: it names the old entries and removes none. It gives the entry, the rule file, and the commands for steps 4, 6, 7 and 8 instead of running them.

**C. Already connected; only install or refresh the rule and command** (e.g. after `client-rules.md` or the templates changed):

```text
Following sections 2 and 3 of the file for this client in <MCP_HUB_PATH>/docs/clients/, install or
refresh the mcp-hub always-on rule and the /mcp-hub command. Replace old copies if there are any.
```

**D. Daily use:** `/mcp-hub <server-id> <task>`, e.g. `/mcp-hub mcp-server-logs show the prod logs for service orders-api` or `/mcp-hub jira-sync-ticket check connection`. In Codex, use `$mcp-hub …`. Without the command, type `mcp-hub:<server-id> <task>`. Each client's file lists its exact forms. The rules are in [client-rules.md](client-rules.md).

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
Find out which client you are running in (your system prompt or product name usually says). If unsure, *ask*. Detect the OS and shell. Open the client's file from §4, and read it fully before step 6.
**Done when** you have the client's file open (or [clients/other.md](clients/other.md)).

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

Also list user skills, commands, or rules that target an old server name or old tool names (e.g. a `log-hub` skill, or rules that call `query_logs`). They may steer the agent away from the Hub. *Ask* whether to update or remove each one; never delete one without a yes.

If the client has the config in two places (e.g. Antigravity's current and older-build paths), apply the same change to every file that exists. Leftover per-server cache folders from the old entries can be left alone.
**Done when** no entry other than `mcp-hub` points at a child repo.

### Step 6: Register `mcp-hub`
Follow §1 of the client's file. Prefer the client's CLI command when it has one. Raise the startup timeout to 60–120 s where the client allows it, because the Hub starts every child before it answers.
**Done when** the entry is saved and the file still parses.

### Step 7: Install the rule and the `/mcp-hub` command
Follow §2 and §3 of the client's file. *Ask* before writing; use user/global level so they work in every workspace.
- **Rule:** [client-rules.md](client-rules.md) tells the agent how to use the Hub (call forms, `env`, writing tools). The Hub also sends it as server instructions, but not every client shows those to the model.
- **Command:** a skill or custom command built from [clients/templates/](clients/templates/). It gives `/mcp-hub <server-id> <task>`. In most clients, `/` commands come only from skills or commands, never from the MCP server's name.

A copied rule does not update itself; when `client-rules.md` or a template changes, copy it again (prompt C in §0). Imports (e.g. Claude Code's `@…/docs/client-rules.md` line) and the Hub's server instructions are always current. A copied rule shows its `Version:` line, so a stale copy is easy to spot.
**Done when** the rule and the command are in place, or the user declined.

### Step 8: Reload and verify
1. Reload and verify as §4 of the client's file says. If the user must restart the app, tell them exactly what to do and continue after they confirm.
2. The client should list tools named `<prefix>__<tool>` (e.g. `jira__check_connection`, `logs__query_logs`).
3. For each child, call its read-only check tool from the `CHECKS` table in `scripts/smoke.py` (today `jira__check_connection` and `logs__list_openobserve_organizations`).
4. Test the command: `/mcp-hub mcp-server-logs list the services in dev` (or the client's form from §5 of its file). The agent should use only `logs__*` tools, with `env: "dev"`.
5. Report to the user: the config file you changed, the entries you removed, where the rule and command were installed, and the tool calls that worked.

**Done when** the read-only calls succeed.

---

## 4. Client files

| Client | File |
|---|---|
| Google Antigravity (App, IDE, `agy` CLI) | [clients/antigravity.md](clients/antigravity.md) |
| Claude Code (CLI, VS Code, JetBrains) | [clients/claude-code.md](clients/claude-code.md) |
| OpenAI Codex (CLI, IDE, app) | [clients/codex.md](clients/codex.md) |
| Cursor (editor, `agent` CLI) | [clients/cursor.md](clients/cursor.md) |
| GitHub Copilot in VS Code | [clients/vscode-copilot.md](clients/vscode-copilot.md) |
| Gemini CLI | [clients/gemini-cli.md](clients/gemini-cli.md) |
| Anything else (Claude Desktop, Copilot CLI, Cline, Roo, Continue, Zed, JetBrains AI, Kiro, OpenCode, Goose, …) | [clients/other.md](clients/other.md) |

Every client file has the same sections: 1 MCP entry, 2 always-on rule, 3 `/mcp-hub` command, 4 reload and verify, 5 daily use, 6 logs and quirks. Shared templates are in [clients/templates/](clients/templates/).

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
| Timeout on start | Run step 4 first (it finishes builds), then raise the client's startup timeout (§1 of its file). |
| A child's tools are missing | That child failed or a tool name is wrong. `smoke.py` shows which (`not connected` / `tool not found`). |
| Every tool appears twice | An old direct entry is still configured. Redo step 5. |
| `/mcp-hub` → "No matching results" (or similar) | The skill or command isn't installed, or the client wasn't reloaded. Redo step 7 with §3 of the client's file. Until then, type `mcp-hub:<server-id> <task>` without the leading `/`. |
| Server not listed | Wrong file or top key (§1 of the client's file), invalid JSON, or client not reloaded. Restore the `.bak` if needed. |

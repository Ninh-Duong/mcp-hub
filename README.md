# mcp-hub

One MCP server for your AI client that gives it your team's tools through a single, safe connection: Jira tickets,
Bitbucket pull requests, logs (OpenObserve or Seq), and your project source code. The Hub starts each tool (a "child
MCP"), keeps it connected, shows the AI only the allowed tools, and logs every call without its data.

**Agents: read [AGENTS.md](AGENTS.md) first.** Its rules are mandatory.

## Install (Windows)

1. **Get mcp-hub.** Copy the address from GitHub's green **Code** button, then in a folder of your choice run:

   ```powershell
   git clone <address you copied>
   ```

2. **Double-click `Setup.cmd`** in the `mcp-hub` folder and answer its yes/no questions (Enter keeps the suggestion).
   It downloads the tools you pick, installs everything they need and the **mcp-hub Status** app, opens each tool's own
   login screen for your accounts, checks that everything works, and copies a message for your AI client.
   Type accounts only there, never into an AI chat.
3. **Paste that message (Ctrl+V) into your AI client** (Claude Code, Codex, Antigravity, Cursor, VS Code, Gemini CLI)
   and restart the client when it asks.

The Hub then starts by itself every time you sign in to Windows. macOS, Linux, or a manual install:
[docs/manual-setup.md](docs/manual-setup.md).

## Daily use

Ask your AI client, naming the tool:

```text
/mcp-hub mcp-jira-sync read ticket ABC-123
/mcp-hub mcp-server-logs show yesterday's errors for orders-api in prod
/mcp-hub mcp-pr-companion summarize my open pull requests
/mcp-hub mcp-read-source where is the payment retry implemented in orders-api
```

- Without the `/mcp-hub` command, write `mcp-hub:<tool> <task>` the same way.
- The AI only reads, unless you ask it to change something (for example to sync a ticket).
- **mcp-hub Status** (Start Menu) shows whether the Hub and each tool are working. It starts, restarts, or stops the
  Hub, and turns tools on or off (⚙ Settings).
- To add a tool, change an account, or repair the install, run `Setup.cmd` again.

## If something does not work

| You see | Do this |
|---|---|
| A tool marked **FAIL** or "needs credentials" | Run `Setup.cmd` again and set up that tool's account. |
| The AI client has no mcp-hub tools | Restart or reload the AI client; if it still has none, paste the Setup message again. |
| The Status app says the Hub is stopped | Click **Start Hub** in the app. |
| Anything else | Run `.\scripts\hubctl.ps1 status` and see [docs/security-and-logging.md](docs/security-and-logging.md) → diagnosing failures. |

## Docs

| Need | Read |
|---|---|
| Architecture and default child MCPs | [docs/parent-child-architecture.md](docs/parent-child-architecture.md) |
| Manual or macOS/Linux setup, what each part does | [docs/manual-setup.md](docs/manual-setup.md) |
| Connect or reconfigure an AI client | [docs/connecting-a-client.md](docs/connecting-a-client.md), [docs/clients/](docs/clients/) |
| What each tool does | [docs/mcps/](docs/mcps/) |
| Faster multi-step reads (`hub__batch`) | [docs/batch.md](docs/batch.md) |
| The Status app | [docs/desktop-app.md](docs/desktop-app.md) |
| Running the shared Windows service | [docs/operations/shared-agent-hub.md](docs/operations/shared-agent-hub.md) |
| Settings, environment variables, routes | [docs/settings.md](docs/settings.md), [docs/reference.md](docs/reference.md) |
| Security, logs, diagnosing failures | [docs/security-and-logging.md](docs/security-and-logging.md) |
| Add your own child MCP | [docs/adding-a-child-mcp.md](docs/adding-a-child-mcp.md), [docs/child-mcp-compatibility.md](docs/child-mcp-compatibility.md) |
| Code layout, QC, and releases (contributors) | [docs/architecture.md](docs/architecture.md), [docs/packaging-and-qc.md](docs/packaging-and-qc.md) |

## License

MIT; see [LICENSE](LICENSE).

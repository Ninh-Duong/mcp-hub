# Parent-child architecture and child MCP setup

This document describes the relationship between `mcp-hub` (the parent orchestrator) and its child MCP servers (satellites), the default child servers, how auto-setup works, and where credentials are configured.

## Overview

```text
               +----------------------------------+
               |             AI Client            |
               | (Antigravity / Codex / ChatGPT)  |
               +-----------------+----------------+
                                 | (HTTP / Stdio Bridge)
                                 v
               +----------------------------------+
               |             mcp-hub              |
               |  (Parent Hub / Gateway / Auth)   |
               +-----------------+----------------+
                                 |
         +-------------+---------+---------+-------------+--------------+
         | (stdio)     | (stdio)           | (stdio)     | (stdio)      | (stdio)
         v             v                   v             v              v
  mcp-server-logs  mcp-jira-sync   mcp-pr-companion  mcp-read-source  mcp-sqlserver
```

- **`mcp-hub`** is the central gateway. It manages client connections, authentication, tool routing, audit logging, and the desktop status app.
- **Child MCPs** live as independent sibling repositories in the same parent folder (e.g. `../mcp-jira-sync`).
- **Isolation:** Each child MCP maintains its own code, configuration, credentials, and upstream integrations. The Hub never reads or stores child credentials.

---

## Default child MCPs

The Hub configures these default child MCPs (the list lives in `config/hub.example.json`):

| Child MCP | Purpose | Upstream Service |
|---|---|---|
| `mcp-server-logs` | Query and analyze operational logs | OpenObserve, Seq |
| `mcp-jira-sync` | Read and synchronize Jira tickets | Jira Cloud / Jira Server |
| `mcp-pr-companion` | Review pull requests and comments | Bitbucket Cloud / Server |
| `mcp-read-source` | Semantic source code search and file context | Local project repositories |
| `mcp-sqlserver` | Read-only investigation of real data (one validated `SELECT` at a time) | Microsoft SQL Server |

### 1-Click auto-setup

`Setup.cmd` (`scripts/setup.ps1`) asks nothing and can be run again at any time. See [Setup and first-run checks](architecture.md#setup-and-first-run-checks) for the boundaries it keeps.
1. **Auto-detection and auto-clone:** The script reads the default children and their GitHub addresses (`repository`) from `config/hub.example.json`. Any child missing next to `mcp-hub` is cloned from that address; a failed clone is reported and does not stop the others.
2. **Environment and runtime preparation:** `hubctl install` installs or verifies Python and uv, syncs the locked Hub environment, and runs `python -m mcp_hub.preflight --install`, which, child by child, installs a missing runtime (Node.js, .NET SDK) and runs the child's own `prepare` step. A child whose step fails does not stop the others.
3. **Checks, child by child, then the Hub:** `scripts/smoke.py` connects to each child in turn and calls its read-only check, then checks that the Hub answers `hub__status`. Each line is `PASS`, `WARN`, or `FAIL`; a `FAIL` never stops the next child, and the exit code is 1 only when something failed.
4. **Accounts are never part of Setup:** nothing in Setup asks for, opens, reads, or writes a password, token, or project path. A child whose account is not filled in yet shows `WARN`. The user fills it in that child's own repository ([below](#post-setup-credential-configuration)), restarts the Hub, and runs the smoke check again. The Desktop Status App also shows such a child as not ready.
5. **AI clients:** Setup looks for Claude Code, Codex, Antigravity, Cursor, VS Code, and Gemini CLI (a command on `PATH` or the client's folder in the user profile) and copies one message that tells an AI client to connect itself to the Hub ([connecting-a-client.md](connecting-a-client.md)). Setup writes no client config itself.

---

## Post-setup credential configuration

Each child MCP manages its own credentials locally:

### 1. `mcp-jira-sync`
- **Location:** `../mcp-jira-sync/`
- **Configuration:** Run the child's interactive setup:
  ```powershell
  cd ../mcp-jira-sync
  uv run python cli.py
  ```
  Follow the prompts to enter your Jira URL, user email, and API token.
- See [docs/mcps/mcp-jira-sync.md](mcps/mcp-jira-sync.md).

### 2. `mcp-pr-companion`
- **Location:** `../mcp-pr-companion/`
- **Configuration:** Copy `.env.example` to `.env` in the child and fill in `BITBUCKET_EMAIL`, `BITBUCKET_API_TOKEN`, and `BITBUCKET_REPOSITORY`. The child starts without them; until they are set, its tools answer `No Bitbucket credentials` and the smoke check shows `WARN`.
- See [docs/mcps/mcp-pr-companion.md](mcps/mcp-pr-companion.md).

### 3. `mcp-server-logs`
- **Location:** `../mcp-server-logs/`
- **Configuration:**
  - For **OpenObserve**: Edit `config/openobserve.dev.env` (and optionally `stg`, `prod`) to set `OPENOBSERVE_URL`, `OPENOBSERVE_EMAIL`, and `OPENOBSERVE_TOKEN`.
  - For **Seq**: Run `node start.mjs --cli` to configure the server URL and API key.
- See [docs/mcps/mcp-server-logs.md](mcps/mcp-server-logs.md).

### 4. `mcp-read-source`
- **Location:** `../mcp-read-source/`
- **Configuration:** Add allowed source project folders to `config/private/projects.json`:
  ```json
  {
    "version": 1,
    "projects": {
      "orders-api": "<path/to/orders-api>"
    }
  }
  ```
- See [docs/mcps/mcp-read-source.md](mcps/mcp-read-source.md).

### 5. `mcp-sqlserver`
- **Location:** `../mcp-sqlserver/`
- **Configuration:** Copy `dbconfig.example.json` to `dbconfig.json` in the child and fill in `server`, `username`, and `password` (optionally `allowed_databases`). A read-only login is recommended: the child's `docs/readonly-login.sql`. The child starts without them; until they are set, its tools answer `not configured` and the smoke check shows `WARN`.
- See [docs/mcps/mcp-sqlserver.md](mcps/mcp-sqlserver.md).

---

## Adding custom child MCPs

Developers can extend `mcp-hub` with custom child MCPs:
1. Clone or create your child repository next to `mcp-hub`: `../my-custom-mcp`.
2. Follow [docs/adding-a-child-mcp.md](adding-a-child-mcp.md) to register the child server in `config/private/hub.json`, define exposed tools, and configure prepare commands.
3. Restart the Hub (`hubctl restart`). The Desktop Status App will detect the new child MCP automatically.

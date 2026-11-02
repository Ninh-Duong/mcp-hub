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
         +-------------+---------+---------+-------------+
         | (stdio)     | (stdio)           | (stdio)     | (stdio)
         v             v                   v             v
  mcp-server-logs  mcp-jira-sync   mcp-pr-companion  mcp-read-source
```

- **`mcp-hub`** is the central gateway. It manages client connections, authentication, tool routing, audit logging, and the desktop status app.
- **Child MCPs** live as independent sibling repositories in the same parent folder (e.g. `../mcp-jira-sync`).
- **Isolation:** Each child MCP maintains its own code, configuration, credentials, and upstream integrations. The Hub never reads or stores child credentials.

---

## Default child MCPs

The Hub configures 4 default child MCPs:

| Child MCP | Purpose | Upstream Service |
|---|---|---|
| `mcp-server-logs` | Query and analyze operational logs | OpenObserve, Seq |
| `mcp-jira-sync` | Read and synchronize Jira tickets | Jira Cloud / Jira Server |
| `mcp-pr-companion` | Review pull requests and comments | Bitbucket Cloud / Server |
| `mcp-read-source` | Semantic source code search and file context | Local project repositories |

### 1-Click auto-setup

When running `Setup.cmd` (`scripts/setup.ps1`):
1. **Auto-detection & Auto-clone:** The setup script checks if the 4 default child repositories exist next to `mcp-hub`. Any missing default repositories are automatically cloned from the user's remote Git host.
2. **Environment & Runtime preparation:** Installs or verifies Git, Node.js, uv, and Python 3.12. Dependencies for both the Hub and all child MCPs are prepared via `preflight.py --install`.
3. **Decoupled credential setup:** The setup completes 100% of the platform installation without prompting for passwords or API tokens. Service and Desktop Status App start immediately.
4. **Status reporting:** Children without credentials start in an unconfigured state (`credentials required`). The Desktop Status App displays this clearly, allowing the user to configure credentials in each child repository at their convenience.

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
- **Configuration:** Run the child's interactive menu:
  ```powershell
  cd ../mcp-pr-companion
  node bin/cli.js
  ```
  Sign in with your Bitbucket username and App Password.
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

---

## Adding custom child MCPs

Developers can extend `mcp-hub` with custom child MCPs:
1. Clone or create your child repository next to `mcp-hub`: `../my-custom-mcp`.
2. Follow [docs/adding-a-child-mcp.md](adding-a-child-mcp.md) to register the child server in `config/private/hub.json`, define exposed tools, and configure prepare commands.
3. Restart the Hub (`hubctl restart`). The Desktop Status App will detect the new child MCP automatically.

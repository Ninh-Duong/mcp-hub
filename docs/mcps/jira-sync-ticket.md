# Jira Ticket Sync MCP contract

- Repository: `D:/Visual Studio Code/jira_sync_ticket`.
- Transport: local stdio process, launched by the Hub.
- Credentials: Hub private config at `config/private/mcps/jira-sync-ticket.json`; the Hub sets `CENTRAL_CONFIG_PATH` for the child and disables `.env` loading for that Hub-launched process. The Jira repo's `.env` is left unchanged for standalone use.
- `jira__check_connection`: read-only Jira profile/connection check. Used for the integration smoke test.
- `jira__list_synced`: reads the local `.ai-context` ticket catalog.
- `jira__sync_ticket`: writes ticket Markdown, changelog, catalog, and downloaded assets under `.ai-context`; do not use for a connectivity smoke test.
- Child diagnostics are written by that repository to its own `logs/app.log`. Hub audit events are written to `mcp-hub/logs/mcp-hub.jsonl`.

# jira-sync-ticket MCP contract

- Repository: `D:/Visual Studio Code/jira_sync_ticket`.
- Transport: local stdio process, launched by the Hub.
- Credentials: the child MCP currently reads its existing ignored `.env`; the Hub does not copy or log those values.
- `jira__check_connection`: read-only Jira profile/connection check. Used for the integration smoke test.
- `jira__list_synced`: reads the local `.ai-context` ticket catalog.
- `jira__sync_ticket`: writes ticket Markdown, changelog, catalog, and downloaded assets under `.ai-context`; do not use for a connectivity smoke test.
- Child diagnostics are written by that repository to its own `logs/app.log`. Hub audit events are written to `mcp-hub/logs/mcp-hub.jsonl`.

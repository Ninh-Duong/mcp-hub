# Jira Ticket Sync MCP contract

- Repository: sibling `../jira_sync_ticket`.
- Transport: local stdio process launched by the Hub (`config/private/mcps/jira-sync-ticket.json`, template `config/mcps/jira-sync-ticket.example.json`).
- Runtime: runs on the Hub's interpreter, `../mcp-hub/.venv/Scripts/python.exe` relative to the child repo (`../mcp-hub/.venv/bin/python` on macOS/Linux). That is why `pyproject.toml` lists `httpx` and `python-dotenv`. They are the child's dependencies; do not remove them as unused.
- Configuration:
  - The private config's `env` sets `CENTRAL_CONFIG_PATH`, which points the child to that same file (relative to the child repo). The child reads its `jira` block: `base_url`, `email`, `api_token`, `max_asset_size_mb`.
  - It also sets `PYTHON_DOTENV_DISABLED`, so the Jira repo's own `.env` is ignored when the Hub launches it. That `.env` is left unchanged for standalone use.

## Tools

- `jira__check_connection`: read-only Jira profile/connection check. Use it for smoke tests.
- `jira__list_synced`: read-only. Lists the local `.ai-context` ticket catalog.
- `jira__sync_ticket`: **writes** ticket Markdown, changelog, catalog, and downloaded assets under `.ai-context`. Never use it for a connectivity test.

## Diagnostics

- Child logs: `../jira_sync_ticket/logs/app.log`. Hub events: `logs/mcp-hub.jsonl`.
- End-to-end check: `uv run python scripts/smoke.py` (calls `jira__check_connection`, read-only).

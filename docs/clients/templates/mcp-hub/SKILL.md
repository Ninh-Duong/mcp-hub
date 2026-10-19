---
name: mcp-hub
description: Run a task through the mcp-hub MCP server with one of its child MCPs. Use when the user types /mcp-hub <server-id> <task>, $mcp-hub <server-id> <task>, or mcp-hub:<server-id> <task>, or asks to use mcp-hub.
argument-hint: "<server-id> <task>"
---

# mcp-hub

The user's input after the command is `<server-id> <task>`. Clients that substitute arguments put it here: $ARGUMENTS. If that shows literally as `$ARGUMENTS`, read the input from the user's message.

1. **Parse.** The first word is the child's server ID (e.g. `mcp-server-logs`, `mcp-jira-sync`). The rest is the task, in any language. If the input is empty or the ID is unknown, list the connected server IDs with one example each, then ask.
2. **Check the Hub.** Hub tools are named `<prefix>__<tool>`, and each description starts with `[<feature> via <server-id>]`. If there are none, tell the user that mcp-hub is not connected or not reloaded, and stop.
3. **Load the child's rules.** Get the Hub prompt named `<server-id>` if this client supports MCP prompts. Otherwise read `docs/mcps/<server-id>.md` in the mcp-hub repo (its path is in this client's `mcp-hub` server entry).
4. **Do the task with that child's tools only.**
   - `dev`, `stg`, or `prod` in the task means the tool's `env` argument; keep it for the whole task.
   - Stay read-only unless the task asks for a write (e.g. `jira__sync_ticket`, `logs__export_openobserve_logs`).
   - Never read the child's repo files to get around a tool.
5. **Answer with evidence.** Say which tools and environment you used, and cite the records you relied on. On `Child MCP call failed … request_id=<n>`, report the tool, server ID, and request ID, and retry at most once.

All rules: the mcp-hub client rules, which the Hub sends as server instructions and the client installs as an always-on rule (`docs/client-rules.md` in the mcp-hub repo).

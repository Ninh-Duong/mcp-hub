---
id: jira-ticket-bug
when: The user gives a Jira issue key or URL for a bug and wants its cause.
servers: [mcp-jira-sync]
tools: [jira__read_ticket, jira__sync_ticket, jira__read_asset]
needs: [logs]
includes: [rcid-bug, service-errors]
---
## Goal

Turn the ticket into the facts a log search needs (an rcid, a service, a time, an environment), then follow the matching playbook below.

## Steps

1. **Read the cached ticket** with `jira__read_ticket` (`issue_key`).
   - Call `jira__sync_ticket` only when the ticket is not cached, or the user asks for fresh data. It is a write, so the plan must list it in `writes`.
   - Never pass `force: true` unless the user asks.
2. **From the description and the history, extract:**
   - the rcid, request ID, or correlation ID;
   - the service or endpoint;
   - the time the problem happened;
   - the environment.

   Read an attached screenshot with `jira__read_asset` only when the text does not give these facts.
3. **If the ticket names a different environment than the plan,** stop and call `hub__plan` again with that environment. Never switch on your own.
4. **Continue with the matching playbook below:**
   - **The ticket has an rcid** → the steps of `rcid-bug`.
   - **It has only a service and a time** → the steps of `service-errors`.
   - **It has neither** → tell the user what is missing; do not search the logs blindly.

## Answer

The answer of the playbook you followed, plus the issue key and the ticket facts you used.

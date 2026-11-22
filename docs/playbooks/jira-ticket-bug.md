---
id: jira-ticket-bug
when: The user gives a Jira issue key or URL for a bug and wants its cause.
servers: [mcp-jira-sync]
tools: [jira__read_ticket, jira__sync_ticket, jira__read_asset]
needs: [logs]
includes: [rcid-bug, service-errors]
---
## Goal

Investigate the cause of a Jira bug, not a general ticket-summary task.

## Steps and tool inputs

1. Ask for project/env/issue_key. `jira__read_ticket(issue_key)` reads ticket and history from encrypted cache. It must precede any sync or asset read.
2. If missing/stale, report cache_missing/cache_stale with the receipt, ask the user for permission, and continue the plan with writes containing jira__sync_ticket. Never refresh a denied/unreadable cache as a workaround.
3. `jira__sync_ticket(issue_key, force=false)` runs only with permission. Report refreshed, then `jira__read_ticket` reads the refreshed evidence. This intentional post-refresh read is allowed; continuation itself does not replay anything. force=true requires a separate explicit request and plan.
4. `jira__read_asset(issue_key, filename)` reads only an attachment listed by ticket evidence, if text is insufficient.
5. Extract rcid/service/time/environment. Fill only missing context through provide_input. Confirm a proposed log time with the user before logs; a different environment/project requires an authorized new plan.
6. Report has_rcid or has_service to enter the explicitly referenced workflow. Ask for missing facts instead of searching blindly. Log targets are required only when a logs stage starts.

## Answer and stop conditions

Return the issue key, ticket facts used, cause/hypothesis and log/source evidence. No rcid/service or no useful evidence: finish insufficient. Access/configuration errors: blocked. Use only offered tools; investigation job specialists follow the prose/tool order without calling unavailable Hub workflow operations.

## Workflow definition

```workflow
{
  "entry": "ticket",
  "inputs": [
    "project",
    "env",
    "issue_key"
  ],
  "subflows": [
    "rcid-bug",
    "service-errors"
  ],
  "nodes": {
    "ticket": {
      "tools": [
        "jira__read_ticket",
        "jira__read_asset"
      ],
      "requires": [
        "issue_key"
      ],
      "reports": {
        "cache_missing": "refresh",
        "cache_stale": "refresh",
        "has_rcid": "@rcid-bug",
        "has_service": "@service-errors"
      },
      "after": {
        "jira__read_asset": "jira__read_ticket"
      }
    },
    "refresh": {
      "tools": [
        "jira__sync_ticket"
      ],
      "requires": [
        "issue_key"
      ],
      "reports": {
        "refreshed": "fresh_ticket"
      }
    },
    "fresh_ticket": {
      "tools": [
        "jira__read_ticket",
        "jira__read_asset"
      ],
      "requires": [
        "issue_key"
      ],
      "reports": {
        "has_rcid": "@rcid-bug",
        "has_service": "@service-errors"
      },
      "after": {
        "jira__read_asset": "jira__read_ticket"
      }
    }
  }
}
```

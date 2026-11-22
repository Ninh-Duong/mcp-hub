---
id: service-errors
when: The user names a service (and maybe a time) and wants to know what is failing in it, without an rcid.
servers: [mcp-server-logs, mcp-read-source]
tools: [logs__list_ai_context_snapshots, logs__read_ai_context, logs__list_openobserve_services,
        logs__get_openobserve_service_logs, logs__find_openobserve_logs_by_rcid, source__search, source__read]
max_limit: 200
needs: [logs]
---
## Goal

Investigate the failing behavior of one service in one confirmed environment and time window.

## Steps and tool inputs

1. Ask for project/env and absolute since/until before log reads. Never infer a one-hour window or widen to 24 hours automatically.
2. `logs__list_ai_context_snapshots(env)` then `logs__read_ai_context(snapshot, file, offset, limit)` when covered. Start with SUMMARY.lhub and its referenced service/error pages. Report coverage using actual receipts.
3. On a cache miss/insufficient cache when service is not yet known, `logs__list_openobserve_services(env, organization, stream)` lists only the plan targets. If the service name is ambiguous, ask the user; fill the selected service once and report service_selected.
4. `logs__get_openobserve_service_logs(env, organization, stream, service, level, from, to, limit)` reads Error rows in the fixed window, limit at most 50. Group only returned rows. Zero rows: finish insufficient, never change scope automatically.
5. Ask which group to investigate when several fit; identify the chosen group in the client-reported decision. If its error row is insufficient, report need_request, bind its rcid, and use `logs__find_openobserve_logs_by_rcid` in the same targets/window.
6. Report findings with needs_source true/false. When needed, `source__search(project, query, limit)` then `source__read(project, path, start_line, max_lines)` checks the cited code. Report source_found and source_read between stages.

## Answer and stop conditions

Return groups/counts only within received data, chosen cause/hypothesis, evidence and next check. Report truncation and missing evidence. Stop after answering. Access errors block; widening time, another service or another environment requires user authorization and a new scoped plan.

## Workflow definition

```workflow
{
  "entry": "cache_list",
  "inputs": [
    "project",
    "env"
  ],
  "nodes": {
    "cache_list": {
      "tools": [
        "logs__list_ai_context_snapshots"
      ],
      "requires": [
        "window",
        "log_target"
      ],
      "reports": {
        "cache_hit": "cache_read",
        "cache_miss": {
          "input": "service",
          "present": "logs",
          "missing": "select_service"
        }
      }
    },
    "cache_read": {
      "tools": [
        "logs__read_ai_context"
      ],
      "requires": [
        "window",
        "log_target"
      ],
      "reports": {
        "cache_insufficient": {
          "input": "service",
          "present": "logs",
          "missing": "select_service"
        },
        "need_source": "source_search",
        "sufficient": "ready"
      }
    },
    "select_service": {
      "tools": [
        "logs__list_openobserve_services"
      ],
      "requires": [
        "window",
        "log_target"
      ],
      "reports": {
        "service_selected": "logs"
      }
    },
    "logs": {
      "tools": [
        "logs__get_openobserve_service_logs"
      ],
      "requires": [
        "service",
        "window",
        "log_target"
      ],
      "reports": {
        "need_request": "request_logs",
        "need_source": "source_search",
        "sufficient": "ready"
      },
      "arguments": {
        "logs__get_openobserve_service_logs": {
          "level": "Error"
        }
      }
    },
    "request_logs": {
      "tools": [
        "logs__find_openobserve_logs_by_rcid"
      ],
      "requires": [
        "rcid",
        "window",
        "log_target"
      ],
      "reports": {
        "need_source": "source_search",
        "sufficient": "ready"
      }
    },
    "source_search": {
      "tools": [
        "source__search"
      ],
      "requires": [
        "project"
      ],
      "reports": {
        "source_found": "source_read",
        "answered": "ready"
      }
    },
    "source_read": {
      "tools": [
        "source__read"
      ],
      "requires": [
        "project"
      ],
      "reports": {
        "source_read": "ready"
      }
    }
  }
}
```

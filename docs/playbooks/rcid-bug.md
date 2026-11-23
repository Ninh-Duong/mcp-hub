---
id: rcid-bug
when: The user gives an rcid, request ID, or correlation ID and wants to know why that request failed.
servers: [mcp-server-logs, mcp-read-source]
tools: [logs__list_ai_context_snapshots, logs__read_ai_context, logs__find_openobserve_logs_by_rcid,
        logs__get_openobserve_service_logs, source__search, source__read]
max_limit: 200
needs: [logs]
---
## Goal

Explain one request failure using bounded logs and relevant source evidence.

## Steps and tool inputs

1. Ask for project, env, rcid and an absolute time window. No default lookback. Use `hub__workflow provide_input` for missing values; keep confirmed scope fixed.
2. `logs__list_ai_context_snapshots(env)` first. Report cache_hit/cache_miss with its receipt. Never batch cache listing with an upstream search.
3. On a hit, `logs__read_ai_context(snapshot, file, offset, limit)` reads the matching export. Report cache_insufficient, or findings with needs_source true/false. Coverage is client-reported.
4. On a miss, `logs__find_openobserve_logs_by_rcid(env, organization, stream, field, rcid, from, to, limit)` uses the exact plan targets/window and at most 50 rows. No matches: finish insufficient; ask before widening scope.
5. If request rows contain no error, report request_without_error, bind the service observed in those rows, then `logs__get_openobserve_service_logs` with level Warning, the same targets/window and limit at most 50.
6. When code is needed, report findings with needs_source true. `source__search(project, query, limit)` finds the stack method/exception; report source_found, then `source__read(project, path, start_line, max_lines)` reads the relevant excerpt (normally 60 lines). Read only searched or user-bound paths. Report source_read.

## Answer and stop conditions

Return cause or hypothesis, timestamp/service and source file:line evidence, confidence and missing information. Stop logs once sufficient. Truncation is a limitation, not a full population count. Access/configuration failures block the task. Finish supported only at ready; otherwise insufficient/blocked. Ask before any wider search or changed environment.

## Workflow definition

```workflow
{
  "entry": "cache_list",
  "inputs": [
    "project",
    "env",
    "rcid"
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
        "cache_miss": "logs"
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
        "cache_insufficient": "logs",
        "need_source": "source_search",
        "sufficient": "ready"
      }
    },
    "logs": {
      "tools": [
        "logs__find_openobserve_logs_by_rcid"
      ],
      "requires": [
        "window",
        "log_target"
      ],
      "reports": {
        "request_without_error": "related_logs",
        "need_source": "source_search",
        "sufficient": "ready"
      }
    },
    "related_logs": {
      "tools": [
        "logs__get_openobserve_service_logs"
      ],
      "requires": [
        "service",
        "window",
        "log_target"
      ],
      "reports": {
        "need_source": "source_search",
        "sufficient": "ready"
      },
      "arguments": {
        "logs__get_openobserve_service_logs": {
          "level": "Warning"
        }
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

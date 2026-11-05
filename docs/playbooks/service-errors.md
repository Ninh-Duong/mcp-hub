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

Find the errors of one service in a narrow window, group them by cause, and explain the most frequent or most recent one in the code.

## Steps

1. **Check for an export first.** Call `logs__list_ai_context_snapshots` with `env`. If an export covers the service and the window, read its `SUMMARY.lhub` with `logs__read_ai_context`, then the `services/<service>/error.lhub` line it points to. Skip to step 4.
2. **If you are not sure of the service name,** call `logs__list_openobserve_services` once with the plan's `organization` and `stream`, and pick the matching name.
3. **Search.** Call `logs__get_openobserve_service_logs` with `env`, `organization`, `stream`, `service`, `level: "Error"`, and `limit: 50`.
   - If the user gave a time, set `from` and `to` to that time ±15 minutes, in ISO 8601 with a timezone.
   - Otherwise set `from: "1h"`.
   - If the search returns 0 rows, widen once to `24h`.
4. **Group the rows** by exception type and message template. For the top group, take one row's rcid. Read that request's rows with `logs__find_openobserve_logs_by_rcid` (`limit: 50`, the same window), only if the error row alone does not explain it.
5. **Find the code.** Call `source__search` with `project` and the exception or method name, then `source__read` only for the cited lines.

## Answer

- The error groups, with their counts and first and last time.
- For the top group: the cause, the evidence rows, the source `file:line`, and your confidence.
- **Next step:** a fix or the next check.

## Never

- Query `Information` or `Debug` levels before you have looked at the errors.
- Use a window wider than `24h` without the user asking, or a `limit` above 50 on the first call.

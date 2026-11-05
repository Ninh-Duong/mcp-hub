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

Find the request's own log rows, then the error and the code that raised it. Read as little as possible: one request is usually a few dozen rows.

## Steps

1. **One `hub__batch` with two reads:**
   - `logs__list_ai_context_snapshots` with `env`. Look for an export that covers this request: its `rcid/<field>-<id>.lhub` file.
   - `logs__find_openobserve_logs_by_rcid` with `env`, the plan's `organization`, `stream`, and `field` (the Targets below), the user's `rcid`, and `limit: 50`.
     - If the user gave a time, set `from` and `to` to that time ±30 minutes, in ISO 8601 with a timezone.
     - Otherwise set `from: "1h"`.
2. **If a snapshot has the request,** read it with `logs__read_ai_context` (`snapshot`, `file: rcid/<field>-<id>.lhub`) instead of searching again.
3. **If the search returned 0 rows,** widen `from` one step at a time: `1h` → `24h` → `7d`. Stop at the first window that returns rows. If `7d` returns nothing, stop and tell the user. The rcid may be wrong, from another environment, or older than the log retention.
4. **From the rows,** take:
   - the service;
   - the timestamp;
   - the first `Error` or `Fatal` row;
   - the exception type and message;
   - the stack frames (file:line).

   Once you have the error row, stop querying logs.
5. **Only if the request's rows contain no error:** make one `logs__get_openobserve_service_logs` call with that `service`, `level: "Warning"`, `from`/`to` set to the request's timestamp ±5 minutes, and `limit: 50`.
6. **Find the code.** Call `source__search` with `project` (the plan's project) and the exception type, method, or file name from the stack. Then call `source__read` only for the cited lines (`start_line` about 20 lines before the frame, `max_lines` 60).

## Answer

- **Cause:** one or two sentences.
- **Evidence:** the log rows that show it (timestamp, service, level, message), and the source `file:line`.
- **Confidence:** high, medium, or low, and what would confirm it.
- **Next step:** a fix or the next check.

## Never

- Search without the rcid, or run SQL over a whole stream "to have a look".
- Use a `limit` above 50 on the first call, or another environment, organization, or stream than the plan's.
- Repeat a call that already returned rows.

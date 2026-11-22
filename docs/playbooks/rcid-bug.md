---
id: rcid-bug
when: The user gives an rcid, request ID, or correlation ID and wants to know why that request failed.
servers: [mcp-server-logs, mcp-read-source]
tools: [logs__list_ai_context_snapshots, logs__read_ai_context, logs__find_openobserve_logs_by_rcid,
        logs__get_openobserve_service_logs, source__search, source__read]
max_limit: 200
needs: [logs]
workflow_cache_list: [logs__list_ai_context_snapshots]
workflow_cache_read: [logs__read_ai_context]
workflow_logs: [logs__find_openobserve_logs_by_rcid, logs__get_openobserve_service_logs]
workflow_source_search: [source__search]
workflow_source_read: [source__read]
---
## Goal

Find the request's own log rows, then the error and the code that raised it. Read as little as possible: one request is usually a few dozen rows.

## Steps

The `hub__plan` and `hub__workflow` operations below apply only to direct rcid-bug calls when the plan returns a Workflow block. When this recipe is included by another playbook, or used by a job specialist, follow the evidence-reading order without those Hub workflow operations; use only the tools actually offered and the scope supplied by the caller. Do not attempt to create a nested plan or call an unavailable workflow tool.

1. **Declare the request.** Pass `rcid`, `project`, and `env` to `hub__plan`. Its workflow fixes an absolute UTC `since`/`until` window (the preceding hour when omitted). Keep this scope throughout the task.
2. **Check cache first.** Call `logs__list_ai_context_snapshots` with `env`. Report `cache_hit` or `cache_miss` using `hub__workflow`, referencing the returned receipt ID. Never batch this lookup with an upstream query.
3. **Read the matching snapshot.** After `cache_hit`, use `logs__read_ai_context` with `snapshot` and `file`. Report `findings` with `needs_source` true/false if sufficient, or `cache_insufficient` before an upstream search. Cache coverage is a client-reported finding, not a Hub-verified fact.
4. **Only on a cache miss or insufficient cache,** call `logs__find_openobserve_logs_by_rcid` with the plan's `env`, `organization`, `stream`, `field`, `rcid`, exact absolute `from`/`to`, and explicit `limit: 50`. No rows means finish `insufficient`; changing the window requires user-authorized re-planning, never an automatic wider search.
5. **From the rows,** take:
   - the service;
   - the timestamp;
   - the first `Error` or `Fatal` row;
   - the exception type and message;
   - the stack frames (file:line).

   Once you have the error row, stop querying logs.
6. **Only if the request's rows contain no error:** make one `logs__get_openobserve_service_logs` call with that `service`, `level: "Warning"`, the same plan window and targets, and `limit: 50`. Report `findings` with receipt refs and explicit `needs_source` before proceeding.
7. **Find the code when needed.** Call `source__search` with `project` and the exception/method/file. Report `source_found` with its receipt, then call `source__read` only for cited lines (`start_line` about 20 lines before the frame, `max_lines` 60). Report `source_read`, then finish `supported` with evidence refs. If source is unnecessary, report `findings` with `needs_source: false` and finish. Errors do not complete stages; access/configuration errors require reporting `access_blocked` with a failed receipt and finishing `blocked`.

## Answer

- **Cause:** one or two sentences.
- **Evidence:** the log rows that show it (timestamp, service, level, message), and the source `file:line`.
- **Confidence:** high, medium, or low, and what would confirm it.
- **Next step:** a fix or the next check.

## Never

- Search without the rcid, or run SQL over a whole stream "to have a look".
- Use a `limit` above 50 on the first call, or another environment, organization, or stream than the plan's.
- Repeat a call that already returned rows.

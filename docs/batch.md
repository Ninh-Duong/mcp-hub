# Parallel reads with `hub__batch`

## What it does

When an AI client gathers data for a task, most of the time goes to the client's own thinking between two tool calls, not to the calls. Measured on this Hub's audit log:

| | Median |
|---|---|
| One tool call (Hub → child MCP → answer) | about 1 s |
| The client's turn between two sequential calls | about 8 s |

In tasks with three or more calls, the tools took about a quarter of the time. So the way to speed a task up is fewer client turns, not faster tools.

`hub__batch` sends 2 to 8 **independent, read-only** calls in **one** request. The Hub runs them in parallel and returns every result in order. Five reads then cost one client turn instead of five.

```json
{"hub_plan_id": "<from hub__plan>", "calls": [
  {"tool": "jira__read_ticket", "arguments": {"issue_key": "ABC-123"}},
  {"tool": "logs__list_ai_context_snapshots", "arguments": {}},
  {"tool": "pr__list_synced_prs", "arguments": {}}
]}
```

Each result is headed `[i] <tool>: ok|error (<ms> ms)`. The `hub_plan_id` comes from `hub__plan`, and every call in the batch is checked against that plan ([usage-policy.md](usage-policy.md)).

## Rules

- **Read-only tools only.** A tool the child does not mark `readOnlyHint: true` refuses the whole batch; writes (`jira__sync_ticket`, `logs__export_openobserve_logs`, `pr__sync_my_open_prs`) are always called on their own.
- **Independent calls only.** A call that needs another call's result (for example a trace ID found in the logs) comes after the batch, on its own.
- **One failure does not spoil the batch.** A failed or timed-out call reports `error`; retry only that one. The batch is an error only when every call failed.
- **Nothing is bypassed.** Each call takes the normal path:
  - the allowlist;
  - its own audit events, as request `<batch request_id>.<i>`;
  - redaction;
  - the result cache;
  - the child's timeout.
- **Limits come from [settings](settings.md):**
  - `settings.batch.maxCalls` (8): the most calls in one batch;
  - `maxPerChild` (4): the most calls one child serves at the same time;
  - `timeoutSeconds` (120): the time the whole batch may take.

Clients learn to use it from the Hub's server instructions and the client rules ("Batch independent reads"). See [security-and-logging.md](security-and-logging.md) for its audit events.

## See the difference yourself

Run the same five reads twice in your AI client, first one by one, then in one batch.

**Before you start:**
- Reload the `mcp-hub` server in the client (or restart the client) so it lists `hub__batch`.
- Run each prompt in a **new chat**.
- Wait **at least 3 minutes** between the two runs. The Hub caches read-only results for up to 120 s, and a cached second run would look fast for the wrong reason.

**Prompt A, one by one:**

```text
Use the mcp-hub tools. Do not use hub__batch. Call these tools one at a time, waiting for each result
before calling the next:
1. logs__list_log_providers
2. logs__list_ai_context_snapshots
3. jira__list_synced
4. pr__list_synced_prs
5. pr__list_my_open_prs
Then say how many items each tool returned, and when you started and finished.
```

**Prompt B, one batch:**

```text
Use the mcp-hub tools. These five reads do not depend on each other. Send them all in ONE hub__batch call:
1. logs__list_log_providers
2. logs__list_ai_context_snapshots
3. jira__list_synced
4. pr__list_synced_prs
5. pr__list_my_open_prs
Then say how many items each tool returned, and when you started and finished.
```

**Expected:**
- **A:** five client turns of about 1 s of tool time plus about 8 s of thinking each, so roughly **40 to 50 s**.
- **B:** one turn with the tools running in parallel, roughly **10 to 15 s**.

The prompts work in any language. All five tools are read-only and need no arguments: four read the children's local caches, and `pr__list_my_open_prs` reads Bitbucket. Swap in the reads your own task needs.

**Measure it from the audit log** (it reads only event names, times, tool names, and durations):

```powershell
uv run python scripts/call_report.py
```

It reports:
- the median tool time against the median gap between calls (the client's thinking);
- how many calls ran inside `hub__batch` (`inside hub__batch` should be above 0 after prompt B);
- the tool sequences clients repeat most.

A repeated sequence of independent reads is a candidate for `hub__batch` in your prompts or client rules. A repeated sequence whose steps depend on each other is a candidate for a composite tool in that child's repository.

If the client still calls the tools one by one in prompt B, it has not loaded `hub__batch` yet: reload the server or restart the client.

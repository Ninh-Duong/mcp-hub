# Usage policy: `hub__plan`

## Why

The rules in [client-rules.md](client-rules.md) are text. Each client decides whether to load and follow them, and some do not. Antigravity, for example, does not show MCP prompts as commands, so a child's contract is never loaded there. Agents then call tools without a plan, switch environments, repeat calls, and run connection checks as a warm-up.

The Hub therefore enforces the rules it can check. Before any child tool runs, a client declares a plan with `hub__plan`. The Hub returns the plan's rules and then refuses calls that break them. This applies to every client in the same way, whatever it does with server instructions, rules, or skills.

## Flow

1. **Declare the plan.** The client calls `hub__plan`:

   ```json
   {
     "task": "Why did order 123 fail in stg?",
     "servers": ["mcp-server-logs", "mcp-jira-sync"],
     "env": "stg",
     "steps": [
       "hub__batch: jira__read_ticket + logs__list_ai_context_snapshots",
       "logs__query_logs for the trace ID"
     ]
   }
   ```

2. **Read the answer.** The Hub returns:
   - a `hub_plan_id`;
   - the tools allowed for each planned child;
   - concise runtime sections of each child's contract (`docs/mcps/<server-id>.md`), plus any configured log targets and SQL database hints. Full setup/diagnostic docs remain available via the child's MCP prompt.
3. **Pass the ID.** Every later call of the task carries `"hub_plan_id": "<id>"`, including `hub__batch`, where it goes at the top level beside `calls`. The Hub adds this argument to every tool schema it publishes and removes it before the call reaches the child.
4. **Plan again to change scope.** For a new task, another child, or another environment the user named, the client calls `hub__plan` again and uses the new ID.

The plan travels as an argument, not as MCP session state, because current MCP protocol versions give the server no lasting session identity per client.

## Playbook confirmation

With `policy.confirmPlaybook` set to `always` (the default), `hub__plan` without a `confirm_token` plans nothing: it returns the playbooks that fit the task, each with what it is for, a choice of no playbook, and a `confirm_token`. The agent asks the user which one to use, then calls `hub__plan` again with the same task, the chosen playbook (or none), and the token. A token is single use, bound to that task text and the offered choices, and expires after `policy.confirmTokenSeconds`. A refused token is a `Policy:` error, and the agent asks the user again. A chosen playbook skips the BitNet planner. `ambiguous` offers only when no single playbook matches the task; `off` never offers.

## Session execution confirmation

Curated per-client execution profiles live in `config/client_profiles.example.json` (covering Antigravity, Claude Code, Codex, Cursor, GitHub Copilot, and Gemini CLI, plus a single-choice `default` fallback), with optional local overrides in `config/private/client_profiles.json` (no secrets; inspect with `uv run python scripts/private_config.py client-status`).

- With `policy.confirmExecution` set to `always` (the default), `hub__plan` combines playbook confirmation and session execution confirmation (`execution_mode`, `orchestrate_model`, `execute_model`) into a single `confirm_token` offer whenever the detected client (from MCP `clientInfo` or the optional `client` argument) has selectable choices. Setting `policy.confirmExecution` to `off` applies the profile defaults without prompting.
- `multi_agent` is offered only on the shared service (so subagents share the in-memory `PlanBook` and `hub_plan_id`); isolated stdio restricts sessions to `single_agent`.
- Confirmed execution choices lock onto the plan and are inherited across `continue_from` continuations without re-prompting; changing them mid-continuation is refused.
- Workflow stages in `hub__workflow` report `active_tier` (`orchestrate` for missing inputs, `interaction` nodes, and terminal `ready`; `execute` for bounded tool-collection nodes, or an explicit optional node `tier`) and an `execution` object with `recommended_model` and `delegate_stage` (`false` whenever `required_writes` is non-empty).

## Playbooks

A plan can name a playbook (`docs/playbooks/<id>.md`, [list](playbooks/README.md)): a recipe for one kind of input, such as an rcid, a Jira key, a service, or a PR. The `hub__plan` description lists each playbook with its `when` line, so every client can choose one.

```json
{"task": "Why did request 0HN7ABC fail?", "playbook": "rcid-bug", "project": "orders", "env": "stg", "rcid": "0HN7ABC"}
```

A plan with a playbook:

- **Children:** takes its `servers` from the playbook. A plan that names `servers` may name only the playbook's.
- **Log targets:** for a playbook that `needs: [logs]`, the plan must name a `project` and an `env` that has log targets in `config/private/projects.json` ([shared-agent-hub.md](operations/shared-agent-hub.md)). The env defaults to the project's `default_env`. Without targets the plan is refused, and the agent is told to ask the user to add them. It never discovers them.
- **Answer:** returns the playbook's steps and the targets to pass: `organization`, `stream`, `field` (the request-ID field), and `project` for source tools.
- **Enforcement:** the plan may call only the playbook's tools, with a `limit` no larger than its `max_limit`, and with exactly those targets.

## Enforced rules

The Hub checks a call against the rules below in this order, and the first rule it breaks is reported.

| Rule (`reason` in the audit log) | Refused when |
|---|---|
| `policy:no_plan` / `policy:unknown_plan` | The call has no `hub_plan_id`, or the ID is unknown. An ID becomes unknown when the plan was dropped after `settings.policy.maxPlans` newer plans, or when the Hub restarted. |
| `policy:server` | The tool belongs to a child the plan does not name. |
| `policy:playbook_tool` | The plan has a playbook that does not list the tool. |
| `policy:limit` | The plan has a playbook and the call's `limit` is above its `max_limit`. |
| `policy:env` | The plan names an `env` and the call passes another one. It is also refused when the tool takes `env` and the call omits it. |
| `policy:target` | The call passes an `organization`, `stream`, `field`, or `project` other than the plan's targets. |
| `policy:write` | The tool may write (its child does not mark it `readOnlyHint: true`), and the plan does not list it in `writes`. `writes` is meant for writes the user explicitly asked for. |
| `policy:health_check` | The tool is a connection check (`settings.policy.healthCheckTools`), no call of the plan has failed yet, and the plan does not set `connection_checks: true`. That flag is meant for when the user asked to check connections. |
| `policy:repeat` | The same tool already succeeded with the same arguments in this plan. Within one `hub__batch`, it is also refused when the same call appears twice. `settings.policy.repeatAfterSeconds` (default `0`, which refuses every repeat) lets an identical successful call run again once that many seconds have passed. A repeat that runs and fails counts as a failed attempt, so `policy:retry` applies to it. |
| `policy:retry` | The same call already failed twice. |

An invalid `hub__plan` is refused with `policy:plan`. That covers an unknown server ID, empty `steps`, or a `writes` entry that is read-only or belongs to another child.

A refused call returns `Policy: <what to do>` to the client and never reaches the child. A `hub__batch` with one refused call is refused as a whole, with the call's index.

## Plan IDs and argument checks

A plan ID is 128 bits, so it cannot be guessed. The Hub adds `hub_plan_id` to each published schema without changing the child's own keywords: the argument is added to the root `properties`, or, for a root that uses `allOf`, `anyOf`, `oneOf` or `$ref`, the root is wrapped so the argument is valid there too.

Known gap: a root that is a `$ref` to a definition with `additionalProperties: false` still refuses `hub_plan_id` when a client validates the call against the published schema. The Hub itself removes the argument before the child call, so calls work; a client that validates strictly must omit `hub_plan_id` for that tool. This is not fixed in this release.

Arguments are checked against the child's input schema in warn mode (`protocol.validateArguments`). A mismatch writes `tool.arguments.invalid` with the field paths and never the values. The call still runs, and this check is not a policy refusal. Batch items get the same check.

## What the Hub cannot check

- **Whether the user asked for a write.** The Hub only makes the agent declare the write up front. The declaration is visible in `hub.plan.created` (`writes`).
- **Whether the steps are a good plan.** Outside the workflows below, `steps` documents intent without enforcing order.
- **Cache coverage and sync-only-when-stale.** These depend on evidence interpretation. Workflows record them as client-reported decisions, never verified facts.

## Client workflow harness

All shipped playbooks have a client-controlled workflow in policy enforce mode. The client chooses actions; the Hub checks scope, dependencies, permissions and progress. No Hub model loop is added. Each playbook owns a validated JSON graph in a fenced `workflow` block; see [writing a playbook](playbooks/README.md#writing-a-playbook). `includes` merges tools/prose only; Jira explicitly references the rcid and service subflows. Graph tools, inputs, targets, references and cycles are checked at startup. Arbitrary scripts/conditions are not executed.

### Context and missing inputs

`hub__plan` accepts project/env plus rcid, issue_key, service, pr_url, query, glob or path as appropriate. Multi-source review playbooks (`ticket-review`, `change-review`) additionally accept `be_project`/`db_project`/`fe_project`, selected `related_issue_keys`, and (for `change-review`) comma-separated relative `changed_paths`. `since` and `until` are absolute timezone-qualified timestamps. No time window is inferred. Missing context returns stage `awaiting_input`, `active_stage`, `missing_inputs` and the available next operations. Ask the user for missing log times before reading logs. The project's existing default environment remains visible in the plan when used.

Use `hub__workflow` action `provide_input` with `inputs` to fill missing context. Values are bounded strings and may only be filled once; provide since/until together. Targets are resolved only from registered project configuration, never from caller-supplied organization/stream values. Jira may read its ticket before log targets exist; logs stay blocked until targets and times are available. Unknown projects/access errors cannot be bypassed. An environment or other locked value cannot change through provide_input or continue_from.

### Workflow operations

`hub__workflow` requires hub_plan_id and action:

| Action | Input and behavior |
|---|---|
| `status` | Returns stage/active_stage, missing_inputs, allowed_tools, required_writes, report_options, scope, busy, receipts, decisions, revision, counters, timings and limitations. |
| `provide_input` | `inputs` object with project, env, rcid, issue_key, service, pr_url, query, glob, path, since or until. Ask the user or identify evidence provenance before supplying a missing value. Existing values stay locked. |
| `report` | `decision` from report_options and `refs` from this stage's actual receipts. `findings` plus needs_source true/false is shorthand for need_source/sufficient. Decisions are explicitly client-reported, not verified conclusions. |
| `finish` | outcome supported/insufficient/blocked and optional same-plan refs. Supported requires ready stage and successful relevant evidence. A blocked workflow can finish only blocked. |

A recognized access/configuration failure immediately blocks the workflow; finish blocked. A failed call does not complete a stage. Only a recognized missing-cache error allows the Jira cache_missing/cache_stale transition; an access/configuration error never does. For an unclassified failure, retry within policy limits or report access_blocked with a failed receipt and stop. Semantic grouping, snapshot coverage, reported freshness and root cause remain client assertions, not automatic truth inferred from arbitrary text.

### Refresh permissions and continuation

Jira sync and many PR readers are annotated readOnlyHint=false because they can refresh encrypted local storage. In particular, pr__get_pr_sync_status is not a read-only freshness probe. Ask permission at the first required write-capable operation; declare each permitted tool in writes. required_writes lists current stage tools that still need permission. No bulk PR sync is part of review.

After the user agrees, call hub__plan with continue_from, the same task/playbook and all scope values from status, and writes containing the existing permissions plus the exact added refresh tools. Playbook confirmation still applies. A continuation requires an idle unfinished non-blocked workflow, identical scope and a nonempty addition of allowed cache-refresh writes. It copies receipts, progress, counters and call history without executing tools, then supersedes the predecessor. Invalid/busy continuation leaves the predecessor unchanged. An explicitly permitted Jira sync allows one intentional read of the new ticket revision; copying a plan never replays completed calls.

The Hub enforces declarations and scope but cannot independently authenticate whether a chat user really granted permission. Clients must wait for the user's answer. Forced refresh (force=true or refresh=true) requires an explicit separate request and a custom scoped plan; the normal workflows do not infer that permission.

### Execution and evidence

Each child call carries workflow metadata at `_meta["mcp-hub/workflow"]` with plan/receipt IDs, stage and allowed tools. Batch metadata maps item indexes under items to their receipts. A client that hides metadata can call status to obtain receipt IDs as ordinary text. Child schemas, structured content, non-text blocks and existing metadata are preserved. Condensed/packed receipts link to full result_id references; hub__get_full_result pages/searches them. Expiry never automatically replays an upstream call.

Scope is checked before execution. A batch is validated as a whole; it cannot cross stages or include a dependent call before its prerequisite evidence. Write-capable tools cannot be batched. One request owns the plan lock; concurrent requests/transitions are refused until it finishes. Cancellation records a cancelled receipt and releases the lock. Receipt count and input size are bounded by policy settings; state lives only with the bounded in-memory plan. Restart/eviction requires a new plan, not replay.

Source reads use searched paths or a relative path explicitly supplied by the user. Search is substring matching across at most 500 files, so counts cannot be claimed as complete repository-wide totals. Result truncation and unknown completeness remain visible. PR context reads follow diff evidence for the same file. When the child reports a source commit/revision, a changed revision blocks further calls rather than mixing revisions; no revision means unknown, not a guaranteed stable view.

Use scripts/call_report.py for aggregate workflow calls/refusals/finishes and tool time, and status for per-plan evidence and progress. Workflow audit events store bounded metadata, not task text, arguments or result content. These metrics do not measure the client's internal token usage or certify its answer.

## Modes

`settings.policy.mode` sets how the Hub applies these rules. Its keys are in [settings.md](settings.md).

| Mode | Effect |
|---|---|
| `enforce` (default) | Refuse calls that break the plan. |
| `warn` | Run them. The result starts with a `Policy warning: …` block, and the Hub logs `tool.call.policy_warned`. Use it to try the policy with a client before you enforce it. |
| `off` | No plan needed. `hub_plan_id` is still accepted and removed. |

Investigation jobs are not affected. Their specialists call children through the job runner, which applies its own limits and approval policy ([shared-agent-hub.md](operations/shared-agent-hub.md)).

## Ticket requirements review

Select ticket-review to review requirements and implementation impact rather than investigate a runtime bug. Read the primary Jira ticket first, summarize gaps, then ask the user to confirm BE/database/optional FE registered aliases and any related ticket keys. No source or related-ticket call is allowed before the interaction-only sources_confirmed decision. At least one source is required for comparison; declining can still return useful ticket questions with an insufficient outcome.

Role aliases must exist in Hub project configuration and in the source child's registration. status lists aliases without roots. A folder mentioned by the user is not authority to read the filesystem or register it silently. Selections may be corrected/cleared before confirmation. After confirmation, adding/replacing a role or related key requires a new scoped task; refresh continuation must preserve the complete selection.

The definition opts into multi_source/multi_ticket; existing playbooks keep their original restrictions. Source calls use only selected aliases. Search receipts carry project, so a path found in BE cannot unlock the same path in DB/FE. Related Jira calls use only selected keys and receipts carry issue_key; attachments and refresh prerequisites must belong to that issue. The primary ticket key stays locked. No transitive issue discovery is implicit.

Read each selected related ticket, refreshing its missing/stale cache only after permission and an actual read attempt. If a related cache remains missing, record it as a limitation and continue useful source analysis; a supported finish requires successful referenced reads for every selected related ticket and source. Empty/bounded source searches never prove an implementation is absent. A recognized access failure blocks instead of triggering another route.

DB repository files are the primary SP/schema evidence. Live DB reads require a separate explicit request and scoped environment/database. The default review does not execute SQL, modify code, comment on Jira or implement proposed API/SP changes. The report separates common/product, BE/DB, FE and QC confirmations, candidate changes, impact evidence/hypotheses and acceptance/regression cases. FE source omitted means FE implementation claims are unverified. Review completion is distinct from implementation readiness.

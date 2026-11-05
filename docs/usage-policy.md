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
   - each child's contract, `docs/mcps/<server-id>.md`. This is the same text as the child's MCP prompt, so clients without prompt support still receive it.
3. **Pass the ID.** Every later call of the task carries `"hub_plan_id": "<id>"`, including `hub__batch`, where it goes at the top level beside `calls`. The Hub adds this argument to every tool schema it publishes and removes it before the call reaches the child.
4. **Plan again to change scope.** For a new task, another child, or another environment the user named, the client calls `hub__plan` again and uses the new ID.

The plan travels as an argument, not as MCP session state, because current MCP protocol versions give the server no lasting session identity per client.

## Playbooks

A plan can name a playbook (`docs/playbooks/<id>.md`, [list](playbooks/README.md)): a recipe for one kind of input, such as an rcid, a Jira key, a service, or a PR. The `hub__plan` description lists each playbook with its `when` line, so every client can choose one.

```json
{"task": "Why did request 0HN7ABC fail?", "playbook": "rcid-bug", "project": "orders", "env": "stg"}
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
| `policy:repeat` | The same tool already succeeded with the same arguments in this plan. Within one `hub__batch`, it is also refused when the same call appears twice. |
| `policy:retry` | The same call already failed twice. |

An invalid `hub__plan` is refused with `policy:plan`. That covers an unknown server ID, empty `steps`, or a `writes` entry that is read-only or belongs to another child.

A refused call returns `Policy: <what to do>` to the client and never reaches the child. A `hub__batch` with one refused call is refused as a whole, with the call's index.

## What the Hub cannot check

- **Whether the user asked for a write.** The Hub only makes the agent declare the write up front. The declaration is visible in `hub.plan.created` (`writes`).
- **Whether the steps are a good plan.** `steps` documents the intent; the Hub does not compare later calls with it. With a playbook it enforces the tools, the limit, and the targets, not the order of the steps.
- **Cache-first and sync-only-when-stale.** These depend on the content of results, which the Hub does not read.

## Modes

`settings.policy.mode` sets how the Hub applies these rules. Its keys are in [settings.md](settings.md).

| Mode | Effect |
|---|---|
| `enforce` (default) | Refuse calls that break the plan. |
| `warn` | Run them. The result starts with a `Policy warning: …` block, and the Hub logs `tool.call.policy_warned`. Use it to try the policy with a client before you enforce it. |
| `off` | No plan needed. `hub_plan_id` is still accepted and removed. |

Investigation jobs are not affected. Their specialists call children through the job runner, which applies its own limits and approval policy ([shared-agent-hub.md](operations/shared-agent-hub.md)).

# Playbooks

A playbook is a recipe for one kind of task: which mcp-hub tools to call, in which order, how narrow each call must be, and when to stop. An agent selects one with `hub__plan` (`playbook: "<id>"`). The Hub returns the steps, together with the project's log targets, and enforces the playbook's limits ([usage-policy.md](../usage-policy.md#playbooks)). The same files serve every client (Antigravity, Codex, Claude Code, …) and the investigation job specialists.

| Playbook | Use it when |
|---|---|
| [rcid-bug](rcid-bug.md) | The user gives an rcid, request ID, or correlation ID. |
| [jira-ticket-bug](jira-ticket-bug.md) | The user gives a Jira issue for a bug. |
| [service-errors](service-errors.md) | The user names a service, without an rcid. |
| [pr-review](pr-review.md) | The user gives a pull request. |

## Writing a playbook

Create `docs/playbooks/<id>.md`. The `id` must match the file name and use lowercase letters, digits, and `-`. Start the file with frontmatter; the Hub reads it at start:

| Key | Required | Meaning |
|---|---|---|
| `id` | yes | The name agents pass to `hub__plan`. |
| `when` | yes | One sentence that says which input this playbook is for. `hub__plan` lists it, so agents choose by it. |
| `servers` | yes | `[a, b]`: the child server IDs the playbook uses. A plan may name only these. |
| `tools` | yes | `[a, b]`: the only Hub tools the plan may call. `hub__batch` is allowed when every call inside it is listed here. |
| `max_limit` | no | The largest `limit` argument any call may pass. |
| `needs` | no | `[logs]`: the plan must name a project and an environment with log targets in `config/private/projects.json`. |
| `includes` | no | `[a, b]`: other playbooks whose servers, tools, and steps are added. Included playbooks cannot include others. |

A list value may continue on indented lines.

After the frontmatter, write for the agent in this order:

- **Goal.**
- **Steps:** exact tool names and arguments, the first narrow call, how to widen it, and when to stop.
- **Answer:** the shape of the reply.
- **Never:** the mistakes this task invites.

Name only tools that are in `config/hub.example.json`. `tests/test_playbooks.py` checks this, and checks that every playbook loads.

A playbook change takes effect at the next Hub start (`hubctl restart`).

# Playbooks

A playbook is a recipe for one kind of task: which mcp-hub tools to call, in which order, how narrow each call must be, and when to stop. An agent selects one with `hub__plan` (`playbook: "<id>"`). The Hub returns the steps, together with the project's log targets, and enforces the playbook's limits ([usage-policy.md](../usage-policy.md#playbooks)). The same files serve every client (Antigravity, Codex, Claude Code, …) and the investigation job specialists.

## File layout

Keep each playbook in its own file. This README is the shared catalog: summarize when to use each playbook and link to its definition. Edit the individual file to change its tools, steps, limits or answer format; do not duplicate those definitions here.

```text
docs/playbooks/
  README.md            # Catalog of all playbooks
  rcid-bug.md          # Investigate one request or correlation ID
  jira-ticket-bug.md   # Start an investigation from a Jira ticket
  ticket-review.md     # Review requirements and compare confirmed source repositories
  service-errors.md    # Investigate errors for one service
  pr-review.md         # Review a pull request
  source-search.md     # Search a registered source repository
```

At startup, the Hub loads each Markdown definition in this directory and skips README.md. It builds the playbook choices for `hub__plan` from each file's `id` and `when`. The catalog is for navigation, not a second runtime configuration. Use `includes` to reuse another playbook's definition instead of copying it.

## Catalog

| Playbook | Use it when |
|---|---|
| [rcid-bug](rcid-bug.md) | The user gives an rcid, request ID, or correlation ID. |
| [jira-ticket-bug](jira-ticket-bug.md) | The user gives a Jira issue for a bug. |
| [ticket-review](ticket-review.md) | Review ticket clarity, API/SP changes and confirmations for product, BE, FE and QC against selected sources. |
| [service-errors](service-errors.md) | The user names a service, without an rcid. |
| [pr-review](pr-review.md) | The user wants a correctness review of a pull request. |
| [source-search](source-search.md) | The user asks to search or count code in a registered repository. |

## Writing a playbook

Create `docs/playbooks/<id>.md`. The `id` must match the file name and use lowercase letters, digits, and `-`. Start the file with frontmatter; the Hub reads it at start:

| Key | Required | Meaning |
|---|---|---|
| `id` | yes | The name agents pass to `hub__plan`. |
| `when` | yes | One sentence that says which input this playbook is for. `hub__plan` lists it, so agents choose by it. |
| `servers` | yes | `[a, b]`: the child server IDs the playbook uses. A plan may name only these. |
| `tools` | yes | `[a, b]`: the only Hub tools the plan may call. `hub__batch` is allowed when every call inside it is listed here. |
| `max_limit` | no | The largest `limit` argument any call may pass. |
| `needs` | no | `[logs]`: log stages require a registered project/environment and targets. Workflow plans may wait for missing inputs; Jira can read its ticket before log targets exist. `[source]`: source access requires a registered project. |
| `includes` | no | `[a, b]`: other playbooks whose servers, tools, and steps are added. Included playbooks cannot include others. |

A list value may continue on indented lines.

After the frontmatter, write for the agent in this order:

- **Goal.**
- **Steps:** exact tool names and arguments, the first narrow call, how to widen it, and when to stop.
- **Answer:** the shape of the reply.
- **Never:** the mistakes this task invites.

Name only tools that are in `config/hub.example.json`. `tests/test_playbooks.py` checks this, and checks that every playbook loads.

A playbook change takes effect at the next Hub start (`hubctl restart`).

## Workflow definitions

Each shipped playbook includes one fenced `workflow` block containing JSON with entry, inputs, nodes and optional entry_choices/subflows. Each node declares tools, requires and reports (event to target stage). A report target may be a fixed stage or a data-only input/present/missing branch; this only tests whether a bound input exists, never executes expressions. Optional arguments declares literal per-tool argument constraints, such as Error before related Warning logs. Optional after maps a tool to a prerequisite tool; diff context must follow the same file's diff. Terminal targets are ready or blocked. Invalid tools, targets, references and graph cycles fail startup. There is no script/condition evaluation.

Use explicit subflows to reference another definition: Jira declares rcid-bug and service-errors and reports to @rcid-bug or @service-errors. One level is supported; recursive/nested subflows are rejected. includes still combines tools/prose, not graphs. Keep graph and prose tool names and branches in agreement.

Every step must document its tool, input provenance, required permission, expected evidence, next decision and stop condition. Generic report/finish actions, waiting context, refresh continuation and receipt semantics are defined once in [usage policy](../usage-policy.md#client-workflow-harness).

| Playbook | Branches to maintain and test |
|---|---|
| rcid-bug | Cache hit/miss/insufficient; request error/no error; optional service/source; missing time; access failure. |
| jira-ticket-bug | Cached/missing/stale ticket; refresh granted/declined; optional listed attachment; rcid/service branch; missing facts; conflicting env. |
| service-errors | Cache hit/miss; service selection; no errors; selected error group; request/source follow-up; truncated data. |
| pr-review | Missing URL; permission before write-capable readers; context/files/diffs; optional same-file context/threads; revision change; limited/no findings. |
| source-search | Search or direct user path; locations/explanation/observed count; no matches; truncated/bounded search; unknown project. |
| ticket-review | Ticket gaps; cache permissions; source/related-ticket confirmation; BE/DB/optional FE comparison; grouped questions and related-feature impact. |

Ticket-review explicitly opts into multi_source. Its interaction-only confirmation node has no tools and requires successful ticket evidence. Select registered be_project/db_project/fe_project aliases and optional related_issue_keys, then report sources_confirmed after the user agrees. Selection may be corrected before confirmation; additions/changes afterwards require a new scoped task. source_coverage requires source-read evidence from every selected alias before review_complete. Other playbooks retain their original project and ticket restrictions.

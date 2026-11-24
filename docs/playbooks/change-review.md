---
id: change-review
when: The user wants to review current workspace git changes or a newly coded feature for shared-caller impact, duplicate new functions, potential bugs requiring requester confirmation, a /ponytail simplification scan, and an end-to-end Mermaid workflow diagram.
servers: [mcp-read-source, mcp-jira-sync]
tools: [jira__read_ticket, jira__sync_ticket, jira__read_asset, source__index, source__find_entry, source__trace, source__callers, source__outline, source__read_symbol, source__search, source__read]
max_limit: 100
---
## Goal

Review uncommitted workspace git changes (or a newly coded feature) across confirmed BE, database, and optional FE repositories, optionally aligned with a Jira ticket. Verify shared-caller impact for every modified function, audit every newly added function against existing codebase helpers, stop for requester confirmation on potential bug risks, run a `/ponytail` simplification scan whose suggestions wait for explicit user approval before any workspace edit, and finish with a clear Mermaid workflow diagram of the feature flow.

## Steps and tool inputs

1. **Inspect local workspace git diff first.** In the active workspace (outside the read-only Hub), inspect `git status` and `git diff` to collect the exact changed hunks, modified functions/methods/SPs/endpoints, newly added functions/helpers, and the comma-separated relative POSIX `changed_paths`. Never leak absolute roots, secrets, or private config files.
2. **Optional Jira ticket context.** When the user provides an `issue_key` (and optional `related_issue_keys`), the workflow starts at `ticket`. Call `jira__read_ticket(issue_key)` first and `jira__read_asset(issue_key, filename)` only for listed attachments when needed. If the cache is missing or stale, report `cache_missing`/`cache_stale`, ask permission for `jira__sync_ticket`, continue via `continue_from` with exact write permission, sync with `force=false`, report `refreshed`, and read the refreshed ticket. Report `ticket_reviewed` to proceed to `related_tickets` (if selected) or `impact_scan`. When no `issue_key` is given, the workflow starts directly at `impact_scan`.
3. **Bind sources and changed paths.** Ensure at least one registered source alias (`be_project`, `db_project`, `fe_project` from `status.source_choices`) and `changed_paths` are bound in `hub__plan` or `hub__workflow(action="provide_input")`. Once the first `source__*` call runs, the selected repositories and `changed_paths` are locked; start a new scoped plan to widen them.
4. **Analyze shared-caller impact and duplicate functions (`impact_scan`).**
   - Use `source__outline(project, path)` or `source__read(project, path, start_line, max_lines)` on `changed_paths` to inspect every changed and newly added symbol.
   - For every **modified** function, method, stored procedure, or endpoint, trace all direct callers with `source__callers(project, symbol, limit)` (exact call-graph lookup for indexed C#/.proto projects) and complement with `source__search(project, query, glob, limit)` across BE, DB, and FE aliases (including SQL/SP, route, and dynamic/reflection references). Use `source__trace(project, symbol)` and `source__read_symbol(project, symbol)` / `source__read` on discovered caller paths to verify whether shared callers are affected or broken by signature, contract, nullability, exception, or behavioral changes.
   - For every **newly added** function or helper, search the codebase with `source__search` (symbol and text), `source__find_entry`, and `source__outline` in related modules to verify whether equivalent functionality already exists. Report concrete reuse candidates and explain why a new function was introduced instead of reusing existing code.
   - Call `source__index(project)` only if an indexed repository's graph needs a freshness check; never pass `force=true` inside a playbook workflow.
   - Ensure source evidence is read from every selected source alias (`source_coverage`), then report `impact_analyzed` with actual `impact_scan` receipts to enter Gate 1 (`confirm_risks`).
5. **Gate 1 — Confirm potential bugs and requester checks (`confirm_risks`).**
   - This is an interaction-only orchestrator stage (`tools: []`). Present the structured Gate 1 findings to the user: changed-files summary, shared-caller impact matrix, new-function vs. existing-code audit, potential bug/regression risks, and concrete items for the requester to check or confirm (business edge cases, backward compatibility, data migration, cross-service callers).
   - **STOP and wait for the requester's reply.** Do not proceed past Gate 1 until the user confirms or clarifies the risk questions.
   - After the user confirms, call `hub__workflow(action="report", decision="risks_confirmed", refs=[...])` to enter `ponytail_check` (or `risks_confirmed_ready` only if the user explicitly skips the `/ponytail` scan).
6. **Gate 2 — `/ponytail` code review scan (`ponytail_check`).**
   - Re-inspect the changed code (and any targeted `source__read` / `source__read_symbol` / `source__search` checks needed) against `/ponytail` standards: YAGNI (remove dead or speculative code/abstractions), standard-library or existing-helper reuse, minimal lines and indirection, and zero unnecessary dependencies or bloat — while preserving every security/trust boundary and explicitly requested requirement.
   - Report `ponytail_complete` with receipts to reach `ready`, then call `hub__workflow(action="finish", outcome="supported", refs=[...])` (or `insufficient`/`blocked` with limitations if a required source could not be verified).
   - **Never apply `/ponytail` edits automatically.** Present every proposed simplification in a clear table and ask the user to confirm which items (if any) to apply to the workspace.
7. **End with a simple Mermaid workflow diagram.** Conclude the final review report with a concise `mermaid` (`flowchart TD` or `sequenceDiagram`) diagram showing how the changed feature executes end-to-end so the requester can verify the flow at a glance.

## Required review report

Respond in the user's language and preserve technical identifiers.

### Gate 1 report (at `confirm_risks`, before waiting for user confirmation)

1. **Change overview:** changed files (`changed_paths`), confirmed source roles/aliases, optional Jira ticket alignment (`issue_key` / `related_issue_keys`), and summary of modified vs. newly added symbols.
2. **Shared-caller impact analysis:** table of `Modified symbol | Callers found (alias:path:line) | Impact assessment (Safe / Breaking / Needs check) | Explanation & evidence`. State clearly when dynamic/non-indexed callers were checked via bounded `source__search` (at most 500 files).
3. **New function vs. existing codebase audit:** table of `New function (path:line) | Existing similar helpers checked | Reuse verdict | Rationale for creating new vs. reusing`.
4. **Potential bug risks & requester checklist:** numbered list of edge cases, null/boundary/concurrency/contract/migration risks, and specific questions the requester must verify before merging.

### Gate 2 final report (at `ready`, after Gate 1 confirmation)

5. **`/ponytail` simplification proposals (confirm before editing):** table of `# | File / symbol | Issue (bloat / YAGNI / over-abstraction / duplicate) | Proposed simpler alternative | Risk / trade-off`. Explicitly ask the user which proposals to apply before modifying any workspace file.
6. **Feature workflow diagram:** one simple `mermaid` flowchart or sequence diagram illustrating the feature's end-to-end flow (entry point -> validation/logic -> shared dependencies/DB/downstream -> output/error paths) so the requester can verify the flow matches intent.
7. **Limitations:** note any unindexed languages checked only via bounded text search, unselected repositories (e.g., FE omitted), or unavailable related tickets, and confirm that no workspace files were edited during the review.

## Never

- Never edit workspace files or apply `/ponytail` suggestions before the user explicitly confirms them.
- Never claim complete repository-wide caller coverage from bounded `source__search` alone; distinguish exact graph callers (`source__callers`) from bounded text search (`<= 500` files).
- Never pass `force=true` to `source__index` inside the workflow, never read unselected repositories, and never include secrets or absolute local paths in reports.

## Workflow definition

```workflow
{
  "entry": "impact_scan",
  "entry_choices": {
    "issue_key": "ticket"
  },
  "inputs": [],
  "multi_source": true,
  "multi_ticket": true,
  "nodes": {
    "ticket": {
      "tools": [
        "jira__read_ticket",
        "jira__read_asset"
      ],
      "requires": [
        "issue_key"
      ],
      "after": {
        "jira__read_asset": "jira__read_ticket"
      },
      "reports": {
        "cache_missing": "refresh",
        "cache_stale": "refresh",
        "ticket_reviewed": {
          "input": "related_issue_keys",
          "present": "related_tickets",
          "missing": "impact_scan"
        }
      }
    },
    "refresh": {
      "tools": [
        "jira__sync_ticket"
      ],
      "requires": [
        "issue_key"
      ],
      "reports": {
        "refreshed": "fresh_ticket"
      }
    },
    "fresh_ticket": {
      "tools": [
        "jira__read_ticket",
        "jira__read_asset"
      ],
      "requires": [
        "issue_key"
      ],
      "after": {
        "jira__read_asset": "jira__read_ticket"
      },
      "reports": {
        "ticket_reviewed": {
          "input": "related_issue_keys",
          "present": "related_tickets",
          "missing": "impact_scan"
        }
      }
    },
    "related_tickets": {
      "tools": [
        "jira__read_ticket",
        "jira__read_asset",
        "jira__sync_ticket"
      ],
      "requires": [
        "related_issue_keys"
      ],
      "ticket_scope": "related",
      "ticket_coverage": true,
      "allow_missing_cache": true,
      "after": {
        "jira__read_asset": "jira__read_ticket"
      },
      "reports": {
        "related_reviewed": "impact_scan"
      }
    },
    "impact_scan": {
      "tools": [
        "source__index",
        "source__find_entry",
        "source__trace",
        "source__callers",
        "source__outline",
        "source__read_symbol",
        "source__search",
        "source__read"
      ],
      "requires": [
        "source_selection",
        "changed_paths"
      ],
      "source_coverage": true,
      "tier": "execute",
      "reports": {
        "impact_analyzed": "confirm_risks"
      }
    },
    "confirm_risks": {
      "tools": [],
      "interaction": true,
      "tier": "orchestrate",
      "evidence_any": [
        "source__read",
        "source__read_symbol",
        "source__callers",
        "source__outline",
        "source__trace"
      ],
      "reports": {
        "risks_confirmed": "ponytail_check",
        "risks_confirmed_ready": "ready"
      }
    },
    "ponytail_check": {
      "tools": [
        "source__read",
        "source__read_symbol",
        "source__outline",
        "source__search",
        "source__callers",
        "source__trace"
      ],
      "tier": "execute",
      "reports": {
        "ponytail_complete": "ready"
      }
    }
  }
}
```

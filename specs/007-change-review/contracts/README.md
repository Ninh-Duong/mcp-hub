# Interface Contracts: Local Change Review

## 1. `hub__plan` and `hub__workflow` `changed_paths` contract

- `_PLAN_SCHEMA` (`src/mcp_hub/planning/policy.py`) accepts optional `"changed_paths": {"type": "string"}` (comma-separated relative file paths from local Git status/diff).
- `hub__workflow(action="provide_input")` accepts `"changed_paths"` in `inputs` (`REVIEW_SELECTION`).
- Validation:
  - Each non-empty comma-separated item is normalized (`\` -> `/`) and must satisfy `safe_path()` (non-empty, relative, no `..`, no `:`).
  - Once `sources_confirmed` is recorded (or after initial fill-once binding), `changed_paths` cannot be silently expanded; widening requires a new scoped `hub__plan`.

## 2. `mcp-read-source` graph toolset (`config/hub.example.json` & `docs/mcps/mcp-read-source.md`)

All 8 read-only tools plus `source__check_connection` are exposed under `features.source.tools`:

| Tool | Role in `change-review` | Path unlocking / receipt behavior |
|---|---|---|
| `source__index` | Inspect or build repository symbol/file index | Scoped to confirmed `project`; `Receipt.paths` stays project-isolated |
| `source__find_entry` | Locate entrypoints and candidate existing helpers | Extracts discovered `path` values into `Receipt.paths` for that `project` |
| `source__trace` | Trace execution flow across call edges for Mermaid diagram & blast radius | Satisfies `SOURCE_EVIDENCE_TOOLS`; extracts discovered `path` values |
| `source__callers` | Find shared callers of modified symbols | Satisfies `SOURCE_EVIDENCE_TOOLS`; extracts `callers[*].path` for that `project` |
| `source__outline` | Inspect symbol declarations in a changed or discovered file | Satisfies `SOURCE_EVIDENCE_TOOLS`; requires unlocked `path` (`paths \| changed_paths`) |
| `source__read_symbol` | Read bounded implementation of a specific symbol | Satisfies `SOURCE_EVIDENCE_TOOLS`; extracts `target.path` / `path` |
| `source__search` | Substring search for duplicate helpers or pattern occurrences (<=500 files, <=100 matches) | Extracts `matches[*].path` into `Receipt.paths` for that `project` |
| `source__read` | Bounded file excerpt read (<=200 lines) | Satisfies `SOURCE_EVIDENCE_TOOLS`; requires unlocked `path` (`paths \| changed_paths`) |

## 3. Two-gate workflow transitions (`change-review`)

- **Gate 1 (`confirm_risks`)**:
  - `hub__workflow` status reports `active_stage: "confirm_risks"`, `active_tier: "orchestrate"`, `delegate_stage: false`, `report_options: ["risks_confirmed", "access_blocked"]`.
  - Requires at least one succeeded `SOURCE_EVIDENCE_TOOLS` receipt (`evidence_any`).
  - Client presents: (1) shared-caller & cross-repo impact, (2) duplicate-function reuse audit & justification for new helpers, (3) optional Jira alignment gaps, and (4) potential bug risks / business questions for confirmation.
- **Gate 2 (`ready` after `ponytail_check`)**:
  - `ponytail_check` runs bounded simplification inspection (`source__outline`, `source__read_symbol`, `source__search`, `source__read`) and reports `ponytail_complete` -> `ready`.
  - At `ready` (`active_tier: "orchestrate"`), client presents:
    1. `/ponytail` simplification audit (stating explicitly that user confirmation is required before any workspace code edit).
    2. A simple Mermaid `flowchart` diagram reflecting the feature's actual verified flow.
  - Client concludes with `hub__workflow(action="finish", outcome="supported"|"insufficient"|"blocked", refs=[...])`.

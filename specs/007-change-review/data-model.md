# Data Model: Local Change Review

## `WorkflowDefinition` (`src/mcp_hub/planning/workflow_definition.py`)

- `SOURCE_ROLES`: `frozenset({"be_project", "db_project", "fe_project"})`
- `REVIEW_SELECTION`: `SOURCE_ROLES | {"related_issue_keys", "changed_paths"}` — selection inputs locked once `sources_confirmed` (or confirmed review scope) is recorded.
- `INPUTS`: includes `"changed_paths"` alongside `SOURCE_ROLES` and existing workflow inputs.
- `SOURCE_EVIDENCE_TOOLS`: `frozenset({"source__read", "source__read_symbol", "source__outline", "source__callers", "source__trace"})` — tools whose succeeded receipts satisfy `source_coverage`, `evidence_any` on Gate 1, and `finish(outcome="supported")`.
- `_NODE` supports:
  - `evidence_any: list[str]` — interaction gate passes when at least one listed tool has a succeeded receipt in `Workflow.receipts`.
  - `tier: "orchestrate" | "execute"` — optional explicit stage tier override (used on `confirm_risks`).

## `Workflow` & `Receipt` (`src/mcp_hub/planning/workflow.py`)

- `Receipt.paths: tuple[str, ...]` — bounded, `safe_path`-validated relative paths extracted from any succeeded `source__*` result (`_extract_paths`), scoped to `Receipt.project`.
- `Workflow.check(tool, arguments)` collects `paths` across succeeded `source__*` receipts where `ref.project == arguments.get("project")` and passes them to `check_scope`.
- `Workflow.report(decision, refs, needs_source)`:
  - On `interaction: true` nodes (`confirm_sources`, `confirm_risks`), validates both `evidence` (all required tools succeeded) and `evidence_any` (at least one listed tool succeeded).
  - On `source_coverage: true` nodes, verifies every confirmed `source_projects(context)` alias has at least one succeeded `SOURCE_EVIDENCE_TOOLS` receipt.
- `Workflow.finish(outcome, refs)` requires `outcome == "supported"` on `multi_source` workflows to reference at least one `SOURCE_EVIDENCE_TOOLS` receipt for every confirmed source alias.

## `changed_paths` & `check_source` (`src/mcp_hub/planning/workflow_scope.py`)

- `fill_context` parses `changed_paths` as comma-separated non-empty relative paths, normalizes `\` to `/`, validates every entry with `safe_path()`, and deduplicates in order.
- `changed_paths(context) -> set[str]` returns the parsed set of relative changed paths bound in `Workflow.context`.
- `check_source` enforces:
  - `project` must belong to confirmed `source_projects(context)` in `multi_source` mode.
  - `source__read`, `source__outline` (and `path`-scoped reads) require `path` to be `safe_path`-valid and present in `paths | user_paths` (same-project succeeded receipts or provenance-checked `changed_paths` / bound `path`).

## `change-review` State Graph (`docs/playbooks/change-review.md`)

- **Entry**: `impact_analysis` (default) or `ticket` when `issue_key` is present (`entry_choices: {"issue_key": "ticket"}`).
- **Optional Jira stages**: `ticket` -> (`refresh` -> `fresh_ticket`) -> `confirm_sources` (`interaction: true`) -> (`related_tickets` if `related_issue_keys` else `impact_analysis`).
- **Source analysis stage (`impact_analysis`)**: `tier: "execute"`, `requires: ["source_selection", "changed_paths"]`, `source_coverage: true`, tools: all 8 read-only `source__*` graph/search/read tools -> reports `analysis_complete` -> `confirm_risks`.
- **Gate 1 (`confirm_risks`)**: `interaction: true`, `tier: "orchestrate"`, `tools: []`, `evidence_any: SOURCE_EVIDENCE_TOOLS` -> reports `risks_confirmed` -> `ponytail_check`.
- **Simplification audit (`ponytail_check`)**: `tier: "execute"`, `requires: ["source_selection", "changed_paths"]`, tools: `["source__outline", "source__read_symbol", "source__search", "source__read"]` -> reports `ponytail_complete` -> `ready`.
- **Gate 2 (`ready`)**: `active_tier: "orchestrate"` -> client presents `/ponytail` simplification audit (requiring confirmation before any workspace edit) and simple Mermaid workflow diagram, then calls `hub__workflow(action="finish", outcome="supported"|"insufficient"|"blocked", refs=[...])`.

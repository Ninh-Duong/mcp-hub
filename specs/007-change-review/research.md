# Research & Key Decisions: Local Change Review

## Decisions

1. **Dedicated `change-review` playbook with optional Jira branch**
   - Add `docs/playbooks/change-review.md` (`id: change-review`) instead of overloading `pr-review` (remote Bitbucket PRs) or `ticket-review` (pre-implementation requirement review).
   - Use `entry_choices: {"issue_key": "ticket"}` so reviews with a Jira `issue_key` read the ticket (and confirmed `related_issue_keys`) before code analysis, while ticketless local change reviews enter source analysis directly once `source_selection` and `changed_paths` are confirmed.

2. **Two-gate orchestration flow (`confirm_risks` -> `ponytail_check` -> `ready`)**
   - Gate 1 (`confirm_risks`) uses `interaction: true`, `tools: []`, `tier: "orchestrate"`, and `evidence_any` set to `SOURCE_EVIDENCE_TOOLS`. This reuses `Workflow`'s existing `interaction` and `active_tier` mechanics while generalizing interaction evidence checks to accept any succeeded graph/read source tool (`evidence_any`).
   - After Gate 1 (`risks_confirmed`), `ponytail_check` runs a bounded `/ponytail` simplification audit using read-only source tools (`source__outline`, `source__read_symbol`, `source__search`, `source__read`) before transitioning to Gate 2 (`ready`), where proposed simplifications and a simple Mermaid workflow diagram are presented and workspace edits remain blocked without explicit user confirmation.

3. **Full read-only `mcp-read-source` graph toolset exposure**
   - Expose `source__index`, `source__find_entry`, `source__trace`, `source__callers`, `source__outline`, `source__read_symbol`, `source__search`, and `source__read` in `config/hub.example.json` (`features.source.tools`) and document every tool's inputs, bounds, and read-only guarantees in `docs/mcps/mcp-read-source.md`.
   - Accept any `SOURCE_EVIDENCE_TOOLS` receipt (`source__read`, `source__read_symbol`, `source__outline`, `source__callers`, `source__trace`) for `source_coverage` and `finish(outcome="supported")` so graph-based inspections count alongside raw line reads.

4. **Provenance-checked `changed_paths` and per-project receipt path unlocking**
   - Normalize and validate `changed_paths` in `workflow_scope.py` (`safe_path`, slash normalization, deduplication, `REVIEW_SELECTION` post-confirmation lock) so only safe relative paths from the user's local Git diff enter workflow scope.
   - Extract discovered relative paths from succeeded `source__*` tool responses (`matches[*].path`, `callers[*].path`, `target.path`, `path`, and graph location fields) onto `Receipt.paths`, tagged by `Receipt.project`, and filter unlocked paths in `Workflow.check` strictly by `ref.project == arguments.get("project")`.

5. **Clean module layering and zero workspace mutation**
   - All changes stay within the existing `planning` (`policy`, `workflow_definition`, `workflow_scope`, `workflow`, `workflow_runtime`), `config/hub.example.json`, `docs/`, and `tests/` boundaries (`tests/test_module_boundaries.py`).
   - `change-review` exposes only read-only `source__*` and `jira__*` tools (plus explicit-permission `jira__sync_ticket`); workspace edits from Gate 2 `/ponytail` findings are proposed to the user and never executed by the playbook.

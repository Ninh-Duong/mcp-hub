# Feature Specification: Local Change Review

**Feature Branch**: `007-change-review`
**Created**: `2026-11-25`
**Status**: `Approved for implementation`
**Input**: Review current local Git changes of a newly implemented feature across confirmed BE/database/optional FE repositories for shared-caller impact, duplicate-function reuse, optional Jira ticket alignment, two-gate user confirmation (`confirm_risks` and `ponytail_check` -> `ready`), and full read-only `mcp-read-source` graph toolset exposure.

## User Scenarios & Testing

### US1 - Review local Git changes for shared-caller impact, duplicate reuse, and optional Jira alignment (P1)

After inspecting current local Git changes (`changed_paths`) and confirming one or more registered source repositories (`be_project`, `db_project`, `fe_project`) plus optional Jira keys (`issue_key`, `related_issue_keys`), the client runs a bounded impact review across the confirmed repositories using `source__callers`, `source__trace`, `source__read_symbol`, `source__search`, and `source__read`. It identifies affected shared callers, checks whether newly added helpers duplicate existing utilities (and explains why any new function was created instead of reusing an existing helper), and optionally compares behavior against the primary and related Jira tickets.

- **Acceptance**:
  - `change-review` works with one or more confirmed registered source roles (`be_project`, `db_project`, `fe_project`) even when no Jira `issue_key` is supplied, and branches through Jira ticket reads first when `issue_key` (and optional `related_issue_keys`) is provided.
  - Impact analysis covers every confirmed source alias (`source_coverage: true`) using graph and read evidence (`SOURCE_EVIDENCE_TOOLS`) before transitioning to user risk confirmation.
  - The review report explicitly documents shared callers, blast radius, duplicate-function justification, and Jira alignment (or marks Jira/FE unverified when omitted).

### US2 - Enforce a two-gate user confirmation flow (P1)

The workflow enforces two explicit user-facing orchestration gates (`tier: "orchestrate"`) separated by bounded analysis stages:
- **Gate 1 (`confirm_risks`, `interaction: true`, `tier: "orchestrate"`)**: Pauses mid-review after impact analysis to present shared-caller impact findings, duplicate-function checks, potential bug risks, and business questions for requester confirmation before proceeding.
- **Gate 2 (`ready` after `ponytail_check`)**: After Gate 1 confirmation (`risks_confirmed`), runs a `/ponytail` simplification audit (`ponytail_check`) and enters `ready`, presenting the `/ponytail` simplification audit (requiring explicit user confirmation before any workspace code edit) alongside a simple Mermaid workflow diagram illustrating the feature's actual flow.

- **Acceptance**:
  - Attempting to report `risks_confirmed` at `confirm_risks` without prior succeeded source evidence (`evidence_any` matching `SOURCE_EVIDENCE_TOOLS`) is refused with a `Policy:` error.
  - `hub__workflow` status reports `active_tier: "orchestrate"` (`delegate_stage: false`) at `awaiting_input`, `confirm_sources` (when Jira branch runs), `confirm_risks`, and `ready`, and switches to `execute` during bounded analysis (`impact_analysis`, `ponytail_check`).
  - No workspace file edits, Jira comments, or live SQL writes occur during the review; any simplification edit proposed in Gate 2 requires explicit user confirmation outside this read-only review.

### US3 - Expose full `mcp-read-source` graph toolset with provenance-checked `changed_paths` and receipt unlocking (P2)

Expose `mcp-read-source`'s full read-only graph toolset (`source__index`, `source__find_entry`, `source__trace`, `source__callers`, `source__outline`, `source__read_symbol`, `source__search`, `source__read`) through `mcp-hub` (`config/hub.example.json` and `docs/mcps/mcp-read-source.md`). Enforce provenance-checked `changed_paths` and per-project source-receipt path unlocking so paths discovered by graph or search calls in one confirmed repository unlock reads only in that same repository.

- **Acceptance**:
  - `config/hub.example.json` and `docs/mcps/mcp-read-source.md` list and document all 8 read-only `source__*` tools plus `source__check_connection`.
  - `changed_paths` rejects absolute paths, drive letters, `..` traversal, and empty inputs, and locks after confirmation unless cleared/updated prior to confirmation.
  - Paths extracted from succeeded `source__*` receipts (`matches[*].path`, `callers[*].path`, `target.path`, `path`, and trace/entry/outline locations) unlock reads only for matching `Receipt.project`, keeping BE/DB/FE repositories isolated.

## Requirements

- **FR-001**: Expose `source__index`, `source__find_entry`, `source__trace`, `source__callers`, `source__outline`, `source__read_symbol`, `source__search`, and `source__read` in `config/hub.example.json` under `features.source.tools`, and document their inputs, bounds, and read-only guarantees in `docs/mcps/mcp-read-source.md`.
- **FR-002**: Add `changed_paths` to `_PLAN_SCHEMA` (`src/mcp_hub/planning/policy.py`), `REVIEW_SELECTION` / `INPUTS` (`src/mcp_hub/planning/workflow_definition.py`), and `fill_context` / `changed_paths()` (`src/mcp_hub/planning/workflow_scope.py`), validating safe relative paths and enforcing post-confirmation scope lock.
- **FR-003**: Extend `Workflow.check` (`src/mcp_hub/planning/workflow.py`) and `check_source` / `_extract_paths` (`src/mcp_hub/planning/workflow_scope.py`) so succeeded `source__*` calls unlock discovered relative paths strictly for the same `project` alias, and provenance-checked `changed_paths` authorize bounded reads/outlines in confirmed repositories without enabling cross-repo path leakage or raw disk fallback.
- **FR-004**: Support `evidence_any` validation on `interaction` nodes and `SOURCE_EVIDENCE_TOOLS` (`source__read`, `source__read_symbol`, `source__outline`, `source__callers`, `source__trace`) coverage checks for `source_coverage` and `finish(outcome="supported")` in `src/mcp_hub/planning/workflow.py`.
- **FR-005**: Add the `change-review` playbook (`docs/playbooks/change-review.md`) with `multi_source: true`, `multi_ticket: true`, optional `issue_key` / `related_issue_keys` Jira alignment entry (`entry_choices`), bounded `impact_analysis`, Gate 1 `confirm_risks` (`interaction: true`, `tier: "orchestrate"`), `ponytail_check`, and Gate 2 `ready`.
- **FR-006**: Require the `change-review` output at Gate 1 and Gate 2 to separate verified code/ticket evidence from assumptions, include the duplicate-function reuse justification, present the `/ponytail` simplification audit (requiring explicit user confirmation before any workspace edit), and render a simple Mermaid workflow diagram of the feature's actual flow.
- **FR-007**: Update `docs/playbooks/README.md`, `docs/usage-policy.md`, `docs/client-rules.md`, `docs/architecture.md`, `README.md`, and `docs/clients/` templates alongside `tests/contract_baseline/` so `scripts/check_docs.py` and `tests/test_surface_contract.py` stay synchronized.
- **FR-008**: Keep runtime evidence private and tracked artifacts 100% English with repo-relative paths only; ship offline unit tests covering all new workflow, scope, provenance, and graph-tool behaviors.

## Success Criteria

- **SC-001**: Offline tests verify `changed_paths` validation/locking, per-project receipt path isolation across graph/search tools, `evidence_any` gate enforcement, and `SOURCE_EVIDENCE_TOOLS` coverage requirements.
- **SC-002**: Offline workflow tests verify both `change-review` paths (source-only and optional `issue_key` + `related_issue_keys`), Gate 1 (`confirm_risks`, `active_tier="orchestrate"`), `ponytail_check`, Gate 2 (`ready`), `continue_from` preservation, and refusal of premature transitions or unselected sources.
- **SC-003**: `tests/test_playbooks.py`, `tests/test_playbook_workflows.py`, `tests/test_surface_contract.py`, and `tests/test_module_boundaries.py` pass with updated catalog docs and baselines.
- **SC-004**: `uv run --no-sync python scripts/package_qc.py` (`ruff`, >=85% coverage, `check_leaks.py`, `check_code_standards.py`, `check_docs.py`, `compileall`) and `uv run --no-sync python scripts/smoke.py` pass cleanly.

## Edge Cases

- **No Jira ticket supplied**: `change-review` skips Jira nodes when `issue_key` is absent and runs directly through source confirmation/impact analysis, marking Jira alignment as not requested.
- **Path traversal or drive-letter path in `changed_paths`**: `fill_context` rejects any segment containing `..`, `:`, or an absolute path with a `WorkflowError` / `Policy:` refusal before storing context.
- **Cross-repo path isolation**: A path returned by `source__callers` or `source__search` on `be_project` unlocks reads only on `be_project`; reading the same relative path on `db_project` or `fe_project` is refused unless discovered there or explicitly in provenance-checked `changed_paths`.
- **Gate 1 without source evidence**: Reporting `risks_confirmed` at `confirm_risks` before at least one `SOURCE_EVIDENCE_TOOLS` receipt succeeds is refused.
- **Missing or stale Jira cache when `issue_key` is set**: Follows the existing `cache_missing` / `cache_stale` -> `continue_from` write-permission flow; never refreshes silently.
- **No matches or truncated graph/search results**: Bounded results remain explicit limitations; never claim zero callers or complete repository coverage from a truncated or empty search.

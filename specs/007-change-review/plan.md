# Implementation Plan: Local Change Review

## Summary and context

Add the `change-review` playbook (`docs/playbooks/change-review.md`) to review local Git changes (`changed_paths`) across confirmed `be_project`, `db_project`, and optional `fe_project` repositories, with optional Jira ticket alignment (`issue_key`, `related_issue_keys`). Expose `mcp-read-source`'s full read-only graph toolset (`source__index`, `source__find_entry`, `source__trace`, `source__callers`, `source__outline`, `source__read_symbol`, `source__search`, `source__read`), harden `changed_paths` provenance and per-project receipt path unlocking, support `evidence_any` and `SOURCE_EVIDENCE_TOOLS` coverage in `Workflow`, and enforce the two-gate confirmation flow (`confirm_risks` and `ponytail_check` -> `ready`).

## Constitution and boundaries

- Follow `AGENTS.md`: 100% English tracked text, repo-relative paths only (no drive letters, home directories, or real emails), functions at most 50 lines, no blocking work on the event loop in tool calls, and offline unit tests covering all new/changed modules.
- Preserve one-way module dependencies (`tests/test_module_boundaries.py`): `config` -> `settings` -> `planning/workflow_definition` -> `planning/workflow_scope` -> `planning/workflow` -> `planning/policy` -> `planning/workflow_runtime`.
- Read-only review guarantee: `change-review` never modifies workspace files, posts Jira comments, or executes live SQL; any `/ponytail` refactoring proposed in Gate 2 requires explicit user confirmation outside the review plan.
- Regenerate `tests/contract_baseline/` via `uv run --no-sync python -m tests.surface_capture` after `hub__plan` / `hub__workflow` descriptions or tool surfaces change.

## Sequence

1. **Graph toolset exposure & contract docs (`T002`)**: Verify/expose all 8 read-only `source__*` tools plus `source__check_connection` in `config/hub.example.json` and document their inputs, bounds, and read-only rules in `docs/mcps/mcp-read-source.md`.
2. **Provenance-checked `changed_paths` & receipt unlocking (`T003`–`T004`)**: Complete `changed_paths` schema/context validation, provenance guards, `_extract_paths` across graph/search receipts, and per-project `Workflow.check` / `check_source` path unlocking in `policy.py`, `workflow_definition.py`, `workflow_scope.py`, and `workflow.py`.
3. **Interaction `evidence_any` & `SOURCE_EVIDENCE_TOOLS` coverage (`T005`)**: Enforce `evidence_any` on `interaction` nodes and `SOURCE_EVIDENCE_TOOLS` coverage/finish checks in `workflow.py` and `workflow_definition.py`.
4. **`change-review` playbook & two-gate flow (`T006`–`T007`)**: Author `docs/playbooks/change-review.md` with optional Jira branch, `impact_analysis`, Gate 1 `confirm_risks` (`interaction: true`, `tier: "orchestrate"`), `ponytail_check`, and Gate 2 `ready` (`/ponytail` simplification audit + Mermaid workflow diagram); verify runtime `active_tier` and `continue_from` behavior.
5. **Docs, baselines & tests (`T008`–`T011`)**: Update `docs/playbooks/README.md`, `docs/usage-policy.md`, `docs/client-rules.md`, `docs/architecture.md`, `README.md`, and `docs/clients/` templates; add offline tests in `tests/test_change_review.py` and existing suites; regenerate `tests/contract_baseline/`; run `package_qc.py` and `smoke.py`.

## Acceptance

- `change-review` enforces source/path confirmation, per-project path isolation, Gate 1 (`confirm_risks`, `active_tier="orchestrate"`), `ponytail_check`, and Gate 2 (`ready`) with zero implicit edits.
- All functions remain <=50 lines and pass `package_qc.py` (including `check_leaks.py`, `check_code_standards.py`, `check_docs.py`, `test_surface_contract.py`, `test_module_boundaries.py`, >=85% coverage) and `smoke.py`.

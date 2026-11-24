# Tasks: Local Change Review

## Phase 1: Specification & Setup

- [X] T001 Record specification, research decisions, data model, contracts, quickstart, checklist, and implementation plan in `specs/007-change-review/`.

## Phase 2: Graph Toolset Exposure & Provenance Scope (US3)

- [X] T002 [P] [US3] Expose `mcp-read-source`'s full read-only graph toolset (`source__index`, `source__find_entry`, `source__trace`, `source__callers`, `source__outline`, `source__read_symbol`, `source__search`, `source__read`) in `config/hub.example.json` and document all tools in `docs/mcps/mcp-read-source.md`.
- [X] T003 [US3] Wire `changed_paths` schema and context validation (`_PLAN_SCHEMA` in `src/mcp_hub/planning/policy.py`, `REVIEW_SELECTION` and `INPUTS` in `src/mcp_hub/planning/workflow_definition.py`, `fill_context` and `changed_paths` in `src/mcp_hub/planning/workflow_scope.py`) with safe relative-path and provenance guards.
- [X] T004 [US3] Extend source-receipt path extraction (`_extract_paths` in `src/mcp_hub/planning/workflow_scope.py`) and project-isolated path unlocking (`Workflow.check` and `check_source`) across `source__search`, `source__find_entry`, `source__trace`, `source__callers`, `source__outline`, `source__read_symbol`, and `source__read`.

## Phase 3: Gate Enforcement & `change-review` Playbook (US1, US2)

- [X] T005 [US2] Enforce `evidence_any` on `interaction` nodes and `SOURCE_EVIDENCE_TOOLS` multi-source `source_coverage` and `finish` checks in `src/mcp_hub/planning/workflow_definition.py` and `src/mcp_hub/planning/workflow.py`.
- [X] T006 [US1] [US2] Author `docs/playbooks/change-review.md` with optional Jira ticket alignment (`issue_key`, `related_issue_keys`), shared-caller impact analysis, duplicate-function reuse audit, Gate 1 (`confirm_risks`, `interaction: true`, `tier: "orchestrate"`), `ponytail_check`, and Gate 2 (`ready` presenting the `/ponytail` simplification audit requiring edit confirmation and a simple Mermaid workflow diagram).
- [X] T007 [US1] [US2] Verify `change-review` `active_tier` transitions (`orchestrate` on `awaiting_input`, `confirm_sources`, `confirm_risks`, and `ready`; `execute` on `impact_analysis` and `ponytail_check`) and `continue_from` preservation in `src/mcp_hub/planning/workflow_runtime.py` and `src/mcp_hub/planning/policy.py`.

## Phase 4: Documentation, Baselines & Verification

- [X] T008 [P] [US1] [US2] [US3] Update `docs/playbooks/README.md`, `docs/usage-policy.md`, `docs/client-rules.md`, `docs/architecture.md`, `README.md`, and `docs/clients/` templates to document `change-review`, the two-gate confirmation flow, and the graph source tools.
- [X] T009 [P] [US1] [US2] [US3] Add offline unit tests in `tests/test_change_review.py` and update `tests/test_playbooks.py`, `tests/test_playbook_workflows.py`, `tests/test_workflow.py`, and `tests/test_workflow_runtime.py` covering graph tool path unlocking/isolation, `changed_paths` provenance, `evidence_any`, Gate 1 (`confirm_risks`), `ponytail_check`, Gate 2 (`ready`), optional Jira alignment, and zero workspace edits.
- [X] T010 [US3] Regenerate `tests/contract_baseline/` via `uv run --no-sync python -m tests.surface_capture` and confirm `tests/test_surface_contract.py` and `tests/test_module_boundaries.py` pass.
- [X] T011 [US1] [US2] [US3] Run `uv run --no-sync python scripts/package_qc.py` and `uv run --no-sync python scripts/smoke.py`, confirming all functions stay <=50 lines with zero leaks or non-English text.

## Validation results

- `uv run --no-sync python scripts/package_qc.py`: PASS (`ruff` PASS, 1,307 unit tests PASS, 92% coverage >= 85%, `leak_check=PASS`, `code_standards=PASS`, `docs_check=PASS`, `compileall` PASS).
- `../mcp-read-source`: `uv run --locked python -m unittest discover tests`: PASS (29 tests, 1 symlink test skipped on Windows); `scripts/benchmark.py` executed and verified.

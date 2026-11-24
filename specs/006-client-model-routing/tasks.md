# Tasks: Client Model Routing

## Phase 1: Specification & Setup

- [X] T001 Record specification, research decisions, data model, contracts, quickstart, and architectural plan in `specs/006-client-model-routing/`.

## Phase 2: Client Profiles & Configuration (US3)

- [X] T002 [P] [US3] Create curated multi-client profile defaults (`antigravity`, `claude-code`, `codex`, `cursor`, `github-copilot`, `gemini-cli`, and 1-choice `default`) in `config/client_profiles.example.json`.
- [X] T003 [US3] Implement `ClientProfile`, `ClientProfileCatalog`, `load_client_profiles`, alias resolution, `shared_service=False` downgrade, and secret-key checks in `src/mcp_hub/client_profiles.py`, and wire `settings.policy.confirmExecution` (`src/mcp_hub/settings.py`), `StartInputs.client_profiles` (`src/mcp_hub/bootstrap.py`), `HubRuntime` (`src/mcp_hub/server.py`), and `client-status` (`scripts/private_config.py`).

## Phase 3: Unified Confirmation & Session Execution Policy (US1)

- [X] T004 [US1] Extend `ConfirmTokens` with `ConfirmOffer` supporting unified playbook + execution confirmation offers and redemption validation in `src/mcp_hub/planning/confirm.py`.
- [X] T005 [US1] Add `SessionExecution`, `Plan.execution`, optional `client`/`execution_mode`/`orchestrate_model`/`execute_model` schema fields, profile validation, `confirmExecution` handling, and `continue_from` immutability checks in `src/mcp_hub/planning/policy.py`.
- [X] T006 [US1] Wire unified offer generation, client detection (`request_client_name`), `shared_service` flag, and execution audit metadata into `PlanningMixin` in `src/mcp_hub/planning/hub_plan.py` and `src/mcp_hub/server.py`.

## Phase 4: Workflow Tier Guidance & Continuation (US2)

- [X] T007 [P] [US2] Add optional `"tier"` (`orchestrate` | `execute`) node schema support in `src/mcp_hub/planning/workflow_definition.py` and automatic `active_tier` / `delegate_stage` derivation in `src/mcp_hub/planning/workflow.py`.
- [X] T008 [US2] Expose `active_tier`, `delegate_stage`, and `execution` (`client`, `mode`, `mechanism`, `active_tier`, `recommended_model`, `orchestrate_model`, `execute_model`, `delegate_stage`) in `hub__workflow` status and workflow call metadata in `src/mcp_hub/planning/workflow_runtime.py`.

## Phase 5: Documentation, Baselines & Verification

- [X] T009 [P] [US1] [US2] [US3] Update `docs/usage-policy.md`, `docs/settings.md`, `docs/architecture.md`, `docs/client-rules.md`, `docs/security-and-logging.md`, `docs/operations/shared-agent-hub.md`, `docs/clients/*.md`, `docs/clients/templates/mcp-hub/*`, and `AGENTS.md` to document `confirmExecution`, client profiles, unified confirmation, and workflow `active_tier` / `delegate_stage`.
- [X] T010 [P] [US1] [US2] [US3] Add offline unit tests for client profile loading/merging/downgrade, unknown/sensitive key rejection, unified `confirm_token` offer/redemption/refusals, `continue_from` preservation, workflow tier switching, and `private_config.py client-status` in `tests/test_client_profiles.py`, `tests/test_plan_confirm.py`, `tests/test_policy.py`, `tests/test_workflow.py`, `tests/test_workflow_runtime.py`, `tests/test_ticket_review.py`, `tests/test_settings.py`, and `tests/test_private_config.py`.
- [X] T011 [US1] Regenerate tool/surface contract baselines with `uv run --no-sync python -m tests.surface_capture` in `tests/contract_baseline/` and confirm `tests/test_surface_contract.py` and `tests/test_module_boundaries.py` pass.
- [X] T012 [US1] [US2] [US3] Run `uv run --no-sync python scripts/private_config.py client-status`, `uv run --no-sync python scripts/package_qc.py`, and `uv run --no-sync python scripts/smoke.py`, and verify all functions stay <=50 lines with zero leaks or non-English text.

## Validation results

- CLI inspection: `uv run --no-sync python scripts/private_config.py client-status` lists `default`, `antigravity`, `claude-code`, `codex`, `cursor`, `gemini-cli`, and `github-copilot` with their modes, models, and `source=default`.
- Ponytail review: Audited full diff for YAGNI, <=50-line function bounds, trust-boundary validation, and hermetic tests. Fixed 7 findings before final QC: rejected unknown/sensitive keys in `client_profiles.json`, bound `confirm_token` strictly to the offered profile key (blocking client omission/switching), blocked explicit `client` mutation on `continue_from`, eliminated redundant `_workflow_status` / double `_execution_status` and duplicate `validate_session_execution` calls, made `HubRuntime` default client profile load skip `PRIVATE_ROOT` for hermetic tests, unified whitespace-only `task` handling, and kept `request_client_name` off the fast-mode child-tool hot path.
- Package QC (`uv run --no-sync python scripts/package_qc.py`): **PASS** — `ruff check` passed, 1,302 unit tests passed (3 environment-dependent skips), 92% overall coverage (`confirm.py` 98%, `hub_plan.py` 95%, `policy.py` 96%, `workflow_runtime.py` 95%, `settings.py` 99%, `server.py` 91%), `leak_check=PASS`, `code_standards=PASS`, `docs_check=PASS`, `compileall` passed.
- Smoke (`uv run --no-sync python scripts/smoke.py`): `mcp-hub` (`hub__status ok, 5/5 children connected`; `hub__plan` confirmed and created with `client_profile=default`, `execution_mode=single_agent`), `mcp-jira-sync`, `mcp-pr-companion`, and `mcp-read-source` **PASS**. External upstream checks failed outside this feature's scope: `mcp-server-logs` (`logs__list_openobserve_organizations`, `request_id=7/8/9`, `error_summary="fetch failed"`) and `mcp-sqlserver` (`sql__check_connection`, `request_id=13`, `error_type="TimeoutError"`). No private account configs were touched; version remains unchanged pending user decision.

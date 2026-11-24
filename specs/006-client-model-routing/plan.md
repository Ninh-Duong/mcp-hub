# Implementation Plan: Client Model Routing

## Summary and context

Add per-client model and execution-mode routing across `hub__plan` and `hub__workflow`. At startup, the Hub loads curated defaults from `config/client_profiles.example.json` merged with optional non-secret overrides in `config/private/client_profiles.json`. When planning, a unified `confirm_token` (`ConfirmOffer`) confirms the playbook and the client's `execution_mode` (`multi_agent` | `single_agent`), `orchestrate_model`, and `execute_model` (respecting `settings.policy.confirmExecution` and downgrading `multi_agent` to `single_agent` in isolated stdio mode). During workflow execution, `hub__workflow` status derives `active_tier` (`orchestrate` vs `execute`), `delegate_stage`, and `active_model` deterministically and preserves confirmed routing across `continue_from`.

## Constitution and boundaries

- `AGENTS.md` rules apply throughout: 100% English tracked text, repo-relative paths only (no machine paths, drive letters, or real emails), functions at most 50 lines, no blocking work on the event loop in tool calls, and offline unit tests importing every new module under `src/mcp_hub/`.
- Module dependencies remain strictly one-way (`tests/test_module_boundaries.py`):
  `config` -> `settings` -> `client_profiles` -> `planning/*` -> `server` (`bootstrap` wires `StartInputs.client_profiles` into `HubRuntime`). `client_profiles.py` never imports `planning`, `server`, or extensions.
- **Ponytail review**: keep `Workflow` tier derivation automatic from existing stage metadata (`missing()`, `interaction`, `TERMINALS`, optional `tier` field in `_NODE`) rather than duplicating `"tier"` edits across all 6 playbook markdown files; unify confirmation inside the existing `ConfirmTokens` gate instead of adding a second token flow.
- Regenerate `tests/contract_baseline/` using `python -m tests.surface_capture` once `_PLAN_SCHEMA` / `hub__plan` / `hub__workflow` surfaces are updated.

## Sequence

1. **Client profile catalog (`T002`–`T003`)**: Add `config/client_profiles.example.json` and `src/mcp_hub/client_profiles.py` (`ClientProfile`, `ClientProfileCatalog`, `load_client_profiles`, alias resolution, `shared_service=False` downgrade, secret-key guard); wire `StartInputs.client_profiles` in `bootstrap.py` and `HubRuntime`, plus `client-status` in `scripts/private_config.py` and `confirm_execution` in `settings.py`.
2. **Unified `confirm_token` & `Plan.execution` (`T004`–`T006`)**: Extend `src/mcp_hub/planning/confirm.py`, `src/mcp_hub/planning/policy.py`, and `src/mcp_hub/planning/hub_plan.py` with `ConfirmOffer`, `SessionExecution`, optional `hub__plan` args (`client`, `execution_mode`, `orchestrate_model`, `execute_model`), profile validation, and `continue_from` immutability enforcement.
3. **Deterministic workflow tier guidance (`T007`–`T008`)**: Add optional `tier` validation in `src/mcp_hub/planning/workflow_definition.py`, `active_tier` / `delegate_stage` derivation in `src/mcp_hub/planning/workflow.py`, and `execution` status exposure in `src/mcp_hub/planning/workflow_runtime.py`.
4. **Docs, baselines & tests (`T009`–`T012`)**: Update `docs/usage-policy.md`, `docs/settings.md`, `docs/architecture.md`, `docs/client-rules.md`; regenerate `tests/contract_baseline/`; add offline unit tests covering profiles, confirmation, policy, workflow tiers, and `private_config.py client-status`; run full `package_qc.py` and `smoke.py`.

## Acceptance

- Every new/updated function in `src/` and `scripts/` is <=50 lines and passes `ruff`, `check_code_standards.py`, `check_leaks.py`, `check_docs.py`, `test_module_boundaries.py`, and `test_surface_contract.py`.
- Unknown/scripted clients (including `smoke.py`) resolve to the 1-choice `default` profile with zero breaking prompts.
- Package QC (>=85% coverage) and live `smoke.py` pass after the final edit.

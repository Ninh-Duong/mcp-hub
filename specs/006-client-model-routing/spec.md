# Feature Specification: Client Model Routing

**Feature Branch**: `006-client-model-routing`
**Created**: `2026-11-24`
**Status**: `Approved for implementation`
**Input**: Confirm per-session execution mode and orchestrate/execute models per connected AI client via `hub__plan`, expose deterministic per-stage execution tier guidance in `hub__workflow`, and manage curated/overridable client profiles without secrets.

## User Scenarios & Testing

### US1 - Confirm client execution mode and models before planning (P1)

Before creating a plan, `hub__plan` uses a single `confirm_token` gate to ask the user (when needed) to confirm the playbook choice along with the per-session execution mode (`multi_agent` vs `single_agent`), orchestration model (`orchestrate_model`), and execution model (`execute_model`) tailored to the connected AI client (`antigravity`, `claude-code`, `codex`, `cursor`, `github-copilot`, `gemini-cli`).

- **Acceptance**: Calling `hub__plan` without `confirm_token` when confirmation is required returns a single unified offer naming the `confirm_token`, playbook choices, and the detected client's supported execution modes and model choices. Redeeming the token binds the confirmed `SessionExecution` to the created `Plan`; omitting or passing unsupported mode/model values is refused with a `Policy:` error.

### US2 - Deterministic stage tier guidance and continuation preservation (P1)

Once a plan is active, `hub__workflow` status exposes deterministic per-stage tier (`orchestrate` vs `execute`), `delegate_stage`, and the confirmed `execution` setup. User interaction, missing inputs (`awaiting_input`), and terminal/synthesis stages stay on `orchestrate`; bounded tool-collection stages switch to `execute` (when `execution_mode` is `multi_agent`, or clearly report single-agent guidance when `single_agent`). `continue_from` continuations preserve the predecessor's confirmed session execution setup without re-prompting or mutating models.

- **Acceptance**: `hub__workflow` status reports `active_tier` (`orchestrate` or `execute`), `delegate_stage`, and `execution`. Transitions from `awaiting_input` or `interaction` to tool-collection nodes and back to synthesis deterministically toggle `active_tier`. A `continue_from` plan inherits the predecessor's `SessionExecution` and refuses attempts to mutate confirmed models or mode.

### US3 - Curated multi-client profiles and private overrides (P2)

Ship curated defaults for all supported clients plus a 1-choice `default` fallback in `config/client_profiles.example.json`, allow user overrides in `config/private/client_profiles.json` (zero secrets), and inspect the effective catalog with `uv run python scripts/private_config.py client-status`.

- **Acceptance**: Missing `config/private/client_profiles.json` falls back cleanly to `config/client_profiles.example.json`. Valid overrides merge per client profile with `source=private` vs `source=example`. Invalid override files raise `ConfigError` at startup without exposing private data. `private_config.py client-status` prints every effective profile cleanly.

## Requirements

- **FR-001**: Define a pure client-profile loader and resolver (`src/mcp_hub/client_profiles.py`) that loads `config/client_profiles.example.json` and optional `config/private/client_profiles.json`, rejects secret-like keys or invalid schemas, and resolves normalized client names (explicit `client` argument or MCP `request_client_name`) to a `ClientProfile` or `default`.
- **FR-002**: Ship curated defaults in `config/client_profiles.example.json` covering `antigravity`, `claude-code`, `codex`, `cursor`, `github-copilot`, `gemini-cli`, and a 1-choice `default` fallback (`single_agent` with a single model choice).
- **FR-003**: Add `settings.policy.confirmExecution` (`always` | `off`, default `always`) in `src/mcp_hub/settings.py` and wire `client_profiles` through `src/mcp_hub/bootstrap.py` to `HubRuntime`.
- **FR-004**: Extend `hub__plan` schema and `ConfirmTokens` (`src/mcp_hub/planning/confirm.py`, `src/mcp_hub/planning/hub_plan.py`, `src/mcp_hub/planning/policy.py`) to accept optional `client`, `execution_mode`, `orchestrate_model`, and `execute_model`, and unify playbook + execution confirmation under a single `confirm_token` (`ConfirmOffer`).
- **FR-005**: Enforce mode and model validation against the resolved `ClientProfile`: when `shared_service=False` (isolated stdio transport), downgrade `multi_agent` capability to `single_agent`; when `confirm_execution` is `always` and the profile offers multiple choices (and no override/off applies), require explicit confirmation via `confirm_token` and refuse omitted or unsupported values.
- **FR-006**: Store immutable `SessionExecution` on `Plan.execution` and preserve it across `continue_from` continuations; refuse any `continue_from` request that attempts to mutate `client`, `execution_mode`, `orchestrate_model`, or `execute_model`.
- **FR-007**: Derive `Workflow.active_tier` (`orchestrate` or `execute`) deterministically from workflow state (`orchestrate` for `awaiting_input`, `interaction` nodes, terminal stages `ready`/`blocked`/`finished`/`superseded`, or explicit `tier="orchestrate"` override; `execute` for active bounded tool-collection nodes unless overridden) and expose `active_tier`, `delegate_stage`, and `execution` in `hub__workflow` status (`src/mcp_hub/planning/workflow.py`, `src/mcp_hub/planning/workflow_runtime.py`).
- **FR-008**: Add `client-status` subcommand to `scripts/private_config.py` and update `docs/` (`usage-policy.md`, `settings.md`, `architecture.md`, `client-rules.md`) and `tests/contract_baseline/` in lockstep with `scripts/check_docs.py` and `scripts/package_qc.py`.

## Success Criteria

- **SC-001**: Offline unit tests cover `client_profiles.py` loading, merging, validation failures, client name normalization, and `shared_service=False` downgrade to `single_agent`.
- **SC-002**: Offline policy/planning tests verify unified `confirm_token` offer and redemption, refusal of omitted/unsupported modes or models, `default` 1-choice auto-selection for unknown/script clients (`smoke.py`), `confirmExecution="off"` behavior, and `continue_from` execution preservation/mutation refusal.
- **SC-003**: Offline workflow tests verify `active_tier` and `delegate_stage` transitions across `awaiting_input`, `interaction`, tool-collection, and terminal stages in both `multi_agent` and `single_agent` modes without modifying the 6 existing playbook JSON blocks.
- **SC-004**: `uv run python scripts/private_config.py client-status`, `uv run python scripts/package_qc.py` (including surface baseline contract tests, `check_leaks.py`, `check_code_standards.py`, `check_docs.py`, and >=85% coverage), and `uv run python scripts/smoke.py` pass.

## Edge Cases

- **Isolated stdio (`shared_service=False`)**: Even when a client profile permits `multi_agent`, an isolated stdio Hub session downgrades `multi_agent` to `single_agent` (and forces `single_agent` if `multi_agent` is requested or defaulted).
- **Unknown or scripted clients (e.g., `smoke.py`)**: Clients not matching a curated profile resolve to the `default` profile, which has exactly one mode (`single_agent`) and one model per tier so scripted flows like `smoke.py` do not stall on extra execution choices when `confirmPlaybook` is `off` or during custom plans.
- **Omitted or unsupported values on token redemption**: Redeeming a `confirm_token` when the profile offers multiple choices while omitting `execution_mode`, `orchestrate_model`, or `execute_model` (or passing a value outside the offered profile) is refused with `Policy:` and consumes or invalidates the invalid attempt safely.
- **Mutation via `continue_from`**: Passing different `execution_mode`, `orchestrate_model`, `execute_model`, or `client` alongside `continue_from` is refused with `Policy:`; omitting them inherits the predecessor's confirmed `SessionExecution`.
- **Missing or malformed private override**: Absent `config/private/client_profiles.json` is valid and uses `example` defaults; malformed JSON, unknown fields, or sensitive keys raise `ConfigError`.

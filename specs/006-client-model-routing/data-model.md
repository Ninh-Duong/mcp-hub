# Data Model: Client Model Routing

## `ClientProfile` (frozen dataclass in `src/mcp_hub/client_profiles.py`)

Represents the resolved execution routing capabilities and defaults for one client identifier:

- `client_id: str` — Canonical key (e.g., `antigravity`, `claude-code`, `codex`, `cursor`, `github-copilot`, `gemini-cli`, or `default`).
- `aliases: tuple[str, ...]` — Normalized MCP `clientInfo.name` aliases mapped to this profile (case-insensitive matching).
- `modes: tuple[str, ...]` — Allowed execution modes (`"multi_agent"`, `"single_agent"`).
- `default_mode: str` — Default mode chosen when `confirm_execution == "off"` or only one mode exists.
- `orchestrate_models: tuple[str, ...]` — Allowed model IDs for reasoning/orchestration stages.
- `default_orchestrate_model: str` — Default orchestration model ID (must belong to `orchestrate_models`).
- `execute_models: tuple[str, ...]` — Allowed model IDs for bounded execution/collection stages.
- `default_execute_model: str` — Default execution model ID (must belong to `execute_models`).
- `source: str` — Provenance label: `"example"` or `"private"`.

## `ClientProfileCatalog` (frozen dataclass in `src/mcp_hub/client_profiles.py`)

Loaded at startup via `bootstrap.load_start_inputs`:

- `default: ClientProfile` — 1-choice fallback profile (`single_agent`, 1 orchestrate model, 1 execute model).
- `profiles: Mapping[str, ClientProfile]` — Keyed by canonical `client_id`.
- `resolve(client_name: str | None, *, shared_service: bool = True) -> ClientProfile` — Matches `client_name` against `client_id` and `aliases`, falling back to `default`, and downgrades `multi_agent` to `single_agent` when `shared_service=False`.

## `SessionExecution` (frozen dataclass in `src/mcp_hub/planning/policy.py`)

Immutable per-plan execution routing contract established at `hub__plan` creation:

- `client: str` — Resolved canonical `client_id`.
- `execution_mode: str` — Confirmed `"multi_agent"` or `"single_agent"`.
- `orchestrate_model: str` — Confirmed model ID for orchestration/user-facing stages.
- `execute_model: str` — Confirmed model ID for bounded tool-collection stages.
- `source: str` — Confirmation source (`"confirmed"`, `"default"`, `"single_choice"`, `"downgraded"`).

## `ConfirmOffer` (frozen dataclass in `src/mcp_hub/planning/confirm.py`)

Single-use token payload stored by `ConfirmTokens`:

- `expires_at: float` — Monotonic deadline (`policy.confirmTokenSeconds`).
- `task_digest: str` — SHA-256 of stripped task text.
- `playbooks: frozenset[str]` — Offered playbook IDs (plus `NONE_CHOICE`) when playbook confirmation is active, or empty/bypassed when only execution confirmation is active.
- `client_id: str` — Resolved canonical profile ID at offer time.
- `modes: frozenset[str]` — Allowed execution modes from the resolved profile (post `shared_service` downgrade).
- `orchestrate_models: frozenset[str]` — Allowed orchestration models from the resolved profile.
- `execute_models: frozenset[str]` — Allowed execution models from the resolved profile.
- `require_playbook: bool` — Whether playbook selection is enforced on redemption.
- `require_execution: bool` — Whether execution mode/model selection is enforced on redemption.

## `Plan.execution` (`src/mcp_hub/planning/policy.py`)

- `Plan.execution: SessionExecution | None = None` — Attached to the `Plan` instance on creation.
- Preserved verbatim when creating a `continue_from` continuation plan; attempts to supply differing `client`, `execution_mode`, `orchestrate_model`, or `execute_model` on `continue_from` raise `PolicyRefused`.

## `Workflow.active_tier` (`src/mcp_hub/planning/workflow.py`)

Computed property / method on `Workflow`:

- `active_tier() -> str` — Returns `"orchestrate"` when `missing()` is non-empty (`awaiting_input`), `stage in TERMINALS`, `node().get("interaction")` is truthy, or `node().get("tier") == "orchestrate"`; otherwise returns `"execute"` (unless overridden by `node().get("tier")`).
- `delegate_stage(execution: SessionExecution | None) -> bool` — `True` when `execution is not None`, `execution.execution_mode == "multi_agent"`, and `active_tier() == "execute"`.

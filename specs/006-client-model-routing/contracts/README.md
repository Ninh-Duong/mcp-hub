# Interface Contracts: Client Model Routing

## 1. `hub__plan` optional arguments

`_PLAN_SCHEMA` in `src/mcp_hub/planning/policy.py` adds four optional string properties (keeping `additionalProperties: false`):

- `client` (optional `string`): Explicit client profile override (e.g., `"antigravity"`, `"claude-code"`, `"codex"`, `"cursor"`, `"github-copilot"`, `"gemini-cli"`). When omitted, the Hub resolves the connected MCP client's `request_client_name`.
- `execution_mode` (optional `string`, enum `["multi_agent", "single_agent"]`): Confirmed execution mode for this plan session.
- `orchestrate_model` (optional `string`): Confirmed model ID for orchestration, user interaction, and final synthesis stages.
- `execute_model` (optional `string`): Confirmed model ID for bounded tool-collection stages.

### Confirmation & validation behavior

- When `policy.confirmPlaybook` or `policy.confirmExecution` requires confirmation (and the resolved profile has multiple execution choices for execution confirmation, or playbook confirmation applies) and `confirm_token` is absent:
  - `hub__plan` returns a single offer message listing the `confirm_token`, playbook choices (if needed), and the resolved client's `modes`, `orchestrate_models`, and `execute_models` with defaults.
- When `confirm_token` is supplied:
  - `ConfirmTokens.redeem` verifies task digest, expiry, single-use, playbook choice (if required), and validates `execution_mode`, `orchestrate_model`, and `execute_model` against the offered `ClientProfile`.
  - Omitting or passing an unsupported mode/model when multiple choices were offered returns `Policy: ...` error.
- When `continue_from` is supplied:
  - The predecessor's `Plan.execution` is inherited; supplying a different `client`, `execution_mode`, `orchestrate_model`, or `execute_model` returns `Policy: ...` error.
- `hub__plan` response summary includes the confirmed `Execution:` line (`client`, `mode`, `orchestrate_model`, `execute_model`) when present.

## 2. `hub__workflow` status output shape

`_workflow_status` in `src/mcp_hub/planning/workflow_runtime.py` includes:

```json
{
  "active_tier": "orchestrate | execute",
  "delegate_stage": false,
  "execution": {
    "client": "antigravity",
    "execution_mode": "multi_agent",
    "orchestrate_model": "gemini-3-pro",
    "execute_model": "gemini-3-flash",
    "active_model": "gemini-3-pro",
    "delegate_stage": false
  }
}
```

- `active_tier` is `"orchestrate"` for `awaiting_input` (missing inputs), `interaction` stages, terminal stages (`ready`, `blocked`, `finished`, `superseded`), or nodes with `tier: "orchestrate"`, and `"execute"` for active bounded tool-collection stages.
- `active_model` equals `orchestrate_model` when `active_tier == "orchestrate"`, else `execute_model`.
- `delegate_stage` is `true` only when `execution_mode == "multi_agent"` and `active_tier == "execute"`.

## 3. `settings.policy.confirmExecution`

In `config/private/hub.json` (`src/mcp_hub/settings.py`):

- Key: `settings.policy.confirmExecution`
- Allowed values: `"always"` (default) | `"off"`
- Behavior:
  - `"always"`: prompts for execution mode and models alongside playbook confirmation whenever the resolved profile offers more than one mode/model choice.
  - `"off"`: skips execution confirmation prompts and applies the resolved profile's `default_mode`, `default_orchestrate_model`, and `default_execute_model` (respecting any explicit valid arguments passed by the client).

## 4. `scripts/private_config.py client-status` CLI contract

Command:

```powershell
uv run python scripts/private_config.py client-status
```

Output format (one line per profile: `"default"` first, then sorted canonical `client_id` keys):

```text
<client_id>: mode=<default_mode> (<mode1>,<mode2>), orchestrate=<m1>,<m2> [default=<default_orchestrate_model>], execute=<e1>,<e2> [default=<default_execute_model>], source=<example|private>
```

Never prints secrets or machine paths.

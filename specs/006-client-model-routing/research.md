# Research & Key Decisions: Client Model Routing

## Decisions

1. **Single unified `confirm_token` (`ConfirmOffer`) for playbook and execution confirmation**
   - Instead of introducing a second round-trip token for model/mode selection, extend `ConfirmTokens` to issue a single `ConfirmOffer` bound to the task text, offered playbook IDs, resolved client profile, and allowed execution modes/models.
   - Minimizes client-turn overhead and avoids breaking existing `confirm_playbook` workflows while enforcing both choices atomically when redeemed.

2. **Automatic `node_tier` derivation (Ponytail simplification)**
   - Rather than editing all 6 existing playbook `workflow` JSON blocks in `docs/playbooks/*.md` to annotate every node manually, derive `node_tier` automatically in `Workflow`:
     - `orchestrate` when `missing()` is non-empty (`awaiting_input`), when `stage` is a terminal stage (`ready`, `blocked`, `finished`, `superseded`), or when `node.get("interaction")` is true.
     - Optional `"tier": "orchestrate" | "execute"` field in `_NODE` schema (`workflow_definition.py`) allows explicit per-node overrides if needed later without breaking existing playbooks.
     - Otherwise `execute` for bounded tool-collection nodes.
   - `delegate_stage` is `True` only when `execution_mode == "multi_agent"` and `active_tier == "execute"`.

3. **Isolated stdio (`shared_service=False`) downgrades `multi_agent` to `single_agent`**
   - Multi-agent subagent delegation is intended for interactive client sessions routed through the shared Hub service (`shared_service=True`). When `HubRuntime` runs in isolated stdio mode (`shared_service=False`), `multi_agent` is stripped/downgraded to `single_agent` so per-process or test/scripted stdio connections stay single-agent and predictable.

4. **Single-choice `default` profile for unknown/scripted clients**
   - `smoke.py` and unknown/generic MCP clients identify outside the curated IDE client IDs (`antigravity`, `claude-code`, `codex`, `cursor`, `github-copilot`, `gemini-cli`).
   - Giving the `default` profile a single mode (`single_agent`) and a single choice for `orchestrate_models` and `execute_models` ensures execution confirmation never adds an unexpected interactive prompt for `smoke.py` or automated scripts.

5. **Clean module layering**
   - `client_profiles.py` sits in the configuration layer alongside `projects.py` (`config` -> `settings` -> `client_profiles` -> `planning/*` -> `server`). It has no imports from `planning`, `server`, or extensions, preserving `tests/test_module_boundaries.py`.
   - `config/private/client_profiles.json` stores non-secret routing preferences only (mode and model IDs, never API keys or tokens); `is_sensitive_key` checks guard against accidental secrets.

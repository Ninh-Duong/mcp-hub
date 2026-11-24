# Quickstart & Validation: Client Model Routing

## 1. Inspect effective client profiles

```powershell
uv run python scripts/private_config.py client-status
```

Expected output lists `default` followed by `antigravity`, `claude-code`, `codex`, `cursor`, `gemini-cli`, and `github-copilot` with `source=example` (or `source=private` for overridden entries in `config/private/client_profiles.json`).

## 2. Sample Antigravity confirmation flow

1. Client calls `hub__plan`:
   ```json
   {
     "task": "Investigate request rcid-123 failure in stg",
     "playbook": "rcid-bug",
     "project": "orders",
     "env": "stg",
     "rcid": "rcid-123"
   }
   ```
2. Hub responds with a unified confirmation offer containing `confirm_token`, playbook choices, and `antigravity` execution choices (`modes`, `orchestrate_models`, `execute_models`).
3. After asking the user, client calls `hub__plan` again with the token and confirmed routing:
   ```json
   {
     "task": "Investigate request rcid-123 failure in stg",
     "playbook": "rcid-bug",
     "project": "orders",
     "env": "stg",
     "rcid": "rcid-123",
     "confirm_token": "<token>",
     "execution_mode": "multi_agent",
     "orchestrate_model": "<chosen-orchestrate-model>",
     "execute_model": "<chosen-execute-model>"
   }
   ```
4. During `awaiting_input` (e.g., missing `since`/`until`), `hub__workflow` status reports `active_tier: "orchestrate"`, `delegate_stage: false`. Once inputs are provided and the workflow enters a bounded log-collection stage, `hub__workflow` status reports `active_tier: "execute"`, `delegate_stage: true`, and `active_model` switches to the confirmed `execute_model`. At `ready` / synthesis, `active_tier` returns to `"orchestrate"`.

## 3. Validation commands

```powershell
uv run python scripts/package_qc.py
uv run python scripts/smoke.py
```

Note: `test_surface_contract.py` requires regenerating `tests/contract_baseline/` via `uv run python -m tests.surface_capture` after `hub__plan` schema changes.

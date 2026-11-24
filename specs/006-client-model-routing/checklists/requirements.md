# Specification Quality Checklist

- [X] All 6 supported clients (`antigravity`, `claude-code`, `codex`, `cursor`, `github-copilot`, `gemini-cli`) and `default` fallback have clear profile resolution and defaults.
- [X] Single unified `confirm_token` (`ConfirmOffer`) covers playbook and execution mode/model confirmation without extra round trips.
- [X] `shared_service=False` downgrade to `single_agent` and `default` 1-choice compatibility for `smoke.py` are explicitly specified.
- [X] `active_tier` (`orchestrate` vs `execute`) and `delegate_stage` derive deterministically without altering existing playbook JSON blocks, and `continue_from` preserves confirmed execution setup.
- [X] Private overrides (`config/private/client_profiles.json`) exclude secrets and are inspectable via `scripts/private_config.py client-status`.
- [X] Offline unit tests, surface contract baseline update, docs sync (`check_docs.py`), and `package_qc.py` / `smoke.py` criteria are measurable.

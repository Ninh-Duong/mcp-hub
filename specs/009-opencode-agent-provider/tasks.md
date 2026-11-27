# Tasks: OpenCode Agent Provider

- [x] T001 Update `src/mcp_hub/agents/config.py` to support `"opencode"` provider and validated optional `base_url`
- [x] T002 Update `src/mcp_hub/agents/providers.py` with `PROVIDER_PACKAGES["opencode"]`, `OpenCodeProvider`, and Chat Completions helpers
- [x] T003 Update `src/mcp_hub/jobs/tools.py` to instantiate `OpenCodeProvider` in `create_provider`
- [x] T004 Add unit tests in `tests/test_agent_config.py`, `tests/test_model_providers.py`, and `tests/test_job_platform.py`
- [x] T005 Run `uv run --no-sync python scripts/package_qc.py` and verify all gates pass

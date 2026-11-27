# Implementation Plan: OpenCode Agent Provider

**Branch**: `009-opencode-agent-provider` | **Date**: 2026-11-27 | **Spec**: [spec.md](spec.md)

## Summary

Add `"opencode"` as a supported investigation agent provider in `config/private/agents.json` (`src/mcp_hub/agents/config.py`), backed by an OpenAI-compatible Chat Completions adapter `OpenCodeProvider` (`src/mcp_hub/agents/providers.py`) defaulting to `https://opencode.ai/zen/v1` and wired into `src/mcp_hub/jobs/tools.py`.

## Technical Context

- **Language/Runtime**: Python 3.12+ (`uv` managed)
- **Existing SDKs**: `openai` (`AsyncOpenAI`), `anthropic`, `google-genai`
- **Testing**: `unittest` + `coverage` via `scripts/package_qc.py` (>= 85% coverage, ruff, code standards <= 50 lines/function, leak check, docs check)
- **Constraints**: Never store secrets in code/docs/tests; `ProviderError` must omit raw SDK errors; `api_key` remains `repr=False`.

## Project Structure

- `src/mcp_hub/agents/config.py` — Add `"opencode"` to `PROVIDERS`, `DEFAULT_OPENCODE_BASE_URL`, `base_url` field + URL validation.
- `src/mcp_hub/agents/providers.py` — Map `"opencode"` to `"openai"` in `PROVIDER_PACKAGES`, add `OpenCodeProvider` and Chat Completions message/tool/response helpers.
- `src/mcp_hub/jobs/tools.py` — Wire `OpenCodeProvider` into `create_provider`.
- `tests/test_agent_config.py`, `tests/test_model_providers.py`, `tests/test_job_platform.py` — Unit tests for config validation, provider round-trip/error sanitization, and factory wiring.

# Feature Specification: OpenCode Agent Provider

**Feature Branch**: `009-opencode-agent-provider`

**Created**: 2026-11-27

**Status**: Draft

**Input**: User description: "Support OpenCode (OpenCode Zen / OpenAI-compatible Chat Completions endpoint with custom base_url) as a model provider in config/private/agents.json for cross-system issue investigation jobs."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Configure OpenCode as an investigation agent provider (Priority: P1)

An operator configures one or more roles (`coordinator`, `logs`, `source`, `jira`, `pr`, `sql`) in `config/private/agents.json` with `"provider": "opencode"`, a model identifier, and an API key (plus an optional `base_url` defaulting to `https://opencode.ai/zen/v1`), and verifies readiness via `scripts/private_config.py agent-status`.

**Why this priority**: Without configuration parsing, validation, and status reporting, operators cannot select OpenCode for investigation roles.

**Independent Test**: Load a temporary `agents.json` with `"provider": "opencode"` via `load_agent_configs` and run `agent-status` to confirm the role reports `configured` without exposing the API key.

**Acceptance Scenarios**:

1. **Given** `config/private/agents.json` has a role with `"provider": "opencode"`, `"model": "<model>"`, and `"api_key": "<key>"`, **When** `load_agent_configs` runs, **Then** the role is marked `configured` with `base_url` defaulting to `https://opencode.ai/zen/v1`.
2. **Given** a role specifies a custom `"base_url"`, **When** `load_agent_configs` validates it, **Then** HTTPS URLs (and loopback HTTP URLs for local testing/proxies) are accepted, while non-HTTPS remote URLs or malformed URLs raise `AgentConfigError`.
3. **Given** `scripts/private_config.py agent-status` runs, **When** `"provider": "opencode"` is configured, **Then** it prints the role, provider, model, and `configured` state without printing the API key.

---

### User Story 2 - Execute coordinator and specialist tool loops via OpenCode (Priority: P1)

When an investigation job runs for a role configured with `"provider": "opencode"`, the Hub sends messages and tool declarations to the OpenCode OpenAI-compatible Chat Completions endpoint, parses tool calls and final text responses, and handles errors safely without leaking credentials or raw SDK error bodies.

**Why this priority**: Core runtime capability needed for `hub__submit_issue` to actually run investigations using OpenCode.

**Independent Test**: Invoke the OpenCode provider adapter with a mocked `AsyncOpenAI` client returning both tool calls and final text responses, and verify request shaping, response parsing, and error sanitization.

**Acceptance Scenarios**:

1. **Given** an investigation specialist or coordinator turn with tool definitions and conversation history, **When** the OpenCode provider completes the turn, **Then** it calls `chat.completions.create` against the configured `base_url` and returns normalized `ModelTurn` text and `ModelToolCall` items.
2. **Given** the OpenCode endpoint returns malformed tool call JSON arguments or raises a client/network exception, **When** the provider handles the response or exception, **Then** it raises `ProviderError` with a sanitized message containing no API key or raw upstream payload.

---

### User Story 3 - Check provider SDK readiness in preflight and job diagnostics (Priority: P2)

Hub job diagnostics and `provider_package_installed("opencode")` reuse the `openai` SDK check so missing-dependency errors accurately report whether the required SDK is installed.

**Why this priority**: Keeps error messages and readiness checks consistent with existing providers (`openai`, `anthropic`, `gemini`).

**Independent Test**: Call `provider_package_installed("opencode")` and verify it returns the same result as `provider_package_installed("openai")`.

**Acceptance Scenarios**:

1. **Given** `"opencode"` is passed to `provider_package_installed`, **When** the `openai` package is importable, **Then** it returns `True`.

---

### Edge Cases

- What happens when `base_url` is omitted or empty for `"opencode"`? It defaults to `https://opencode.ai/zen/v1`.
- What happens when `base_url` is provided on a non-`opencode` provider (e.g., `gemini` or `anthropic`)? It is rejected with `AgentConfigError` (or ignored only if unset/empty).
- What happens when `base_url` contains embedded credentials (e.g., `https://user:pass@host`) or a non-HTTP(S) scheme? `AgentConfigError` is raised.
- What happens when a Chat Completions response returns `content` as `None` alongside `tool_calls`? Normalized `ModelTurn.text` is `""` and `tool_calls` are parsed cleanly.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: `PROVIDERS` in `src/mcp_hub/agents/config.py` MUST include `"opencode"`.
- **FR-002**: `AgentConfig` MUST support an optional `base_url: str | None = None` field, validated to allow `https://` URLs (or `http://127.0.0.1` / `http://localhost` for loopback) with no embedded userinfo.
- **FR-003**: When `provider == "opencode"` and `base_url` is omitted or empty, the runtime provider MUST default `base_url` to `"https://opencode.ai/zen/v1"`.
- **FR-004**: `src/mcp_hub/agents/providers.py` MUST provide an OpenCode provider adapter using `AsyncOpenAI(api_key=..., base_url=..., timeout=..., max_retries=...)` and `client.chat.completions.create(...)`, translating Hub messages/tools to Chat Completions format and parsing `ModelTurn` / `ModelToolCall` responses.
- **FR-005**: `PROVIDER_PACKAGES` and provider factory/dispatch MUST map `"opencode"` to the `openai` package and the OpenCode provider adapter.
- **FR-006**: All existing security guarantees MUST hold: `ProviderError` must omit raw SDK messages, `api_key` must remain `repr=False` and never be logged, and documentation (`docs/security-and-logging.md`, `docs/architecture.md`, `docs/settings.md` if applicable) must stay in sync with `scripts/check_docs.py`.

### Key Entities *(include if feature involves data)*

- **AgentConfig**: Per-role configuration (`name`, `provider`, `model`, `api_key`, `base_url`) loaded from `config/private/agents.json`.
- **OpenCodeProvider**: `ModelProvider` implementation targeting OpenAI-compatible Chat Completions endpoints (`https://opencode.ai/zen/v1` by default).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: `load_agent_configs` accepts `"provider": "opencode"` (with default and custom `base_url`) and rejects invalid `base_url` values in unit tests.
- **SC-002**: Mocked unit tests verify tool-call round-trips, empty/text responses, and sanitized `ProviderError` handling for `OpenCodeProvider`.
- **SC-003**: `uv run --no-sync python scripts/package_qc.py` passes all checks (ruff, unit tests, coverage >= 85%, leak check, code standards, docs check, compileall).

## Assumptions

- OpenCode Zen (`https://opencode.ai/zen/v1`) exposes an OpenAI-compatible `/chat/completions` API accepting `api_key` via Bearer authentication.
- Existing `openai` Python SDK dependency in `pyproject.toml` is reused; no new external dependency is added.

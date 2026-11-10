# Hub Turn & Payload Speed Optimization (Option C)

Status: done

## Context

End-to-end profiling of a real cross-system debugging session (`logs` + `sql` over `mcp-hub`) showed 899s total wall time across 63 tool turns (632s LLM thinking / 267s tool execution), with 46 `mcp-hub` turns consuming 633s (70.4%). Replay simulation against 22 real condensed payloads revealed:

- `BitnetCondenser._condense` + `_format_capped` discards 100% of compact 1-line JSON outputs (`> 6000` chars) to `"[... 1 more lines omitted]"` while still calling local BitNet (`+2-3s`/item).
- `condenseMinChars` (`6000`) condensed payloads under client inline limits (`~10KB`), and `hub__get_full_result` double-escaped JSON strings (`json.dumps`), lacked `contains`/`regex`/`fields` search or multi-ID fetch, and was blocked inside `hub__batch` + `policy.check_batch`.
- `hub__plan` embedded full setup/build/diagnostics contracts (`~17.9KB` for two children), spilling to file and contradicting policy in `docs/mcps/mcp-sqlserver.md`.
- `BatchSettings.max_per_child` (`4`) serialized 6-8 query batches, catalog/discovery `cacheSeconds` (`120s`) expired within one 15-minute debug session, `RuleCondenser` was absent when BitNet was off, and `projects.json` lacked optional SQL database hints.

## User Stories

- **US1 (P1):** As an AI coding client, compact single-line JSON and medium results arrive losslessly inline (no 0-byte `[... 1 more lines omitted]` truncation, no wasted BitNet calls on JSON, and `condenseMinChars=9000`).
- **US2 (P1):** As an AI coding client, `hub__get_full_result` returns compact raw text by default, works both inside `hub__batch` and via `result_ids`, and supports `contains`/`regex`/`fields` filtering to avoid full-file spills.
- **US3 (P1):** As an AI coding client, `hub__plan` returns concise runtime contracts (`< 9KB` for logs+sql), accurate non-contradictory rules, optional SQL database hints from `projects.json`, and higher catalog cache TTLs / batch concurrency (`maxPerChild=8`, batch-level output packing).
- **US4 (P2):** As an operator running `mcp-hub` without BitNet, deterministic structural condensation and `FullResultStore` stay available out of the box with zero local LLM memory overhead.

## Functional Requirements

- **FR-001:** Core `src/mcp_hub/condense.py` provides deterministic `RuleCondenser`, fixed `format_capped` (never emitting 0-byte omission on single overlong lines), lossless uniform table compaction preserving `_null_cols`, and task-aware shrinkage without importing `bitnet`.
- **FR-002:** `BitnetCondenser` skips LLM calls for JSON and short/few-line (`< 5` lines) inputs, delegating JSON to core structural compaction.
- **FR-003:** `FullResultStore` supports structured JSON/line `contains`, `regex`, and `fields` filtering; `hub__get_full_result` defaults to `"raw"` header+payload (preserving optional `format="json"`), accepts single `result_id` or `result_ids: list[str]`, and is allowed inside `hub__batch` (including policy bypass for internal retrieval).
- **FR-004:** `hub__batch` applies batch-level output packing against `settings.batch.pack_chars` (default `9500`) and defaults `max_per_child` to `8`.
- **FR-005:** `BitnetSettings.condense_min_chars` defaults to `9000`; `service.install_extensions` (when BitNet is off) and `serve()` attach core `RuleCondenser` + `FullResultStore`.
- **FR-006:** `hub__plan` uses concise runtime contracts (`runtime_contract`), and `docs/mcps/mcp-sqlserver.md` removes the contradictory startup `sql__check_connection` guidance.
- **FR-007:** `config/hub.example.json` increases static SQL/Logs catalog `cacheSeconds` (`120` -> `600`).
- **FR-008:** `projects.py` / `policy.py` / `jobs/runner.py` support optional per-environment `sql` database hints (`default_database`, `databases`) without breaking callers lacking `sql`.

## Success Criteria

- **SC-001:** Replay over compact 1-line JSON results returns non-empty valid data (`0/N` zero-byte omissions) and 100% lossless output for uniform tables fitting `<= 9000` chars.
- **SC-002:** Filtered `hub__get_full_result` extracts matching rows/lines in raw format cleanly under 10KB; `hub__plan` for `logs` + `sql` fits under 10KB inline.
- **SC-003:** `uv run --no-sync python scripts/package_qc.py` passes completely (ruff, unittest, coverage >= 85%, leak check, code standards <= 50 lines/function & English-only, docs check, compileall).

## Decisions

Agreed with the user (`Option C` + explicit confirmations):

- Scope strictly limited to `mcp-hub` repository (`wec.be` and child MCP repos untouched).
- `hub__get_full_result` defaults to raw text + 1-line metadata header (`format="raw"`), keeping optional `format="json"`.
- Multi-result retrieval supports both `hub__get_full_result` inside `hub__batch` AND `result_ids: list[str]`.
- Default `settings.bitnet.condense_min_chars = 9000`.
- Condenser operates deterministically even when BitNet is disabled.
- Plan tracked in `agent-plans/`; verification runs offline QC (`uv run --no-sync python scripts/package_qc.py`), user restarts shared service manually afterward.

## Out of scope

- Any edits to `../mcp-sqlserver`, `../mcp-server-logs`, or workspace application repositories.
- Changing child MCP tool schemas upstream.

## Tasks

- [x] **T001** Create core deterministic `src/mcp_hub/condense.py`, add `lossless` flag on `Condensed` (`src/mcp_hub/extensions.py`), and update `src/mcp_hub/bitnet/condense.py` (tests: `tests/test_bitnet_condense.py`, `tests/test_condense.py`)
- [x] **T002** Add search/projection (`contains`, `regex`, `fields`) in `src/mcp_hub/full_results.py` and default raw + `result_ids` + `format` in `src/mcp_hub/server.py` (tests: `tests/test_full_results.py`, `tests/test_server.py`)
- [x] **T003** Allow `hub__get_full_result` in `hub__batch` & policy (`src/mcp_hub/batch.py`, `src/mcp_hub/policy.py`, `src/mcp_hub/server.py`) and add batch-level packing (tests: `tests/test_batch.py`, `tests/test_policy.py`, `tests/test_server.py`)
- [x] **T004** Update `Settings` (`src/mcp_hub/settings.py`: `condense_min_chars=9000`, `max_per_child=8`, `pack_chars=9500`), wire core condenser when BitNet off (`src/mcp_hub/service.py`, `src/mcp_hub/server.py`), update `config/hub.example.json` catalog TTLs (tests: `tests/test_settings.py`, `tests/test_service.py`, `tests/test_config_examples.py`)
- [x] **T005** Add `runtime_contract` trimming in `src/mcp_hub/server.py` for `hub__plan` and fix `docs/mcps/mcp-sqlserver.md` (tests: `tests/test_policy.py`, `tests/test_server.py`)
- [x] **T006** Add optional `sql` hints to `src/mcp_hub/projects.py`, `src/mcp_hub/policy.py`, `src/mcp_hub/jobs/runner.py`, `scripts/private_config.py` (tests: `tests/test_projects.py`, `tests/test_policy.py`, `tests/test_issue_workflow.py`)
- [x] **T007** Update docs in sync (`docs/settings.md`, `docs/bitnet.md`, `docs/architecture.md`, `docs/reference.md`, `docs/client-rules.md`, `docs/batch.md`, `docs/usage-policy.md`, `docs/operations/shared-agent-hub.md`) and run `scripts/package_qc.py`
- [x] **T008** Replay benchmark verification against offline test fixtures and close out `agent-plans/2026-11-11-hub-speed-optimization.md`

## Traceability Matrix

| Requirement | Tasks | Primary Files |
|---|---|---|
| FR-001 | T001 | `src/mcp_hub/condense.py`, `src/mcp_hub/extensions.py` |
| FR-002 | T001 | `src/mcp_hub/bitnet/condense.py` |
| FR-003 | T002, T003 | `src/mcp_hub/full_results.py`, `src/mcp_hub/server.py`, `src/mcp_hub/batch.py`, `src/mcp_hub/policy.py` |
| FR-004 | T003, T004 | `src/mcp_hub/server.py`, `src/mcp_hub/settings.py`, `src/mcp_hub/batch.py` |
| FR-005 | T004 | `src/mcp_hub/settings.py`, `src/mcp_hub/service.py`, `src/mcp_hub/server.py` |
| FR-006 | T005 | `src/mcp_hub/server.py`, `docs/mcps/mcp-sqlserver.md` |
| FR-007 | T004 | `config/hub.example.json` |
| FR-008 | T006 | `src/mcp_hub/projects.py`, `src/mcp_hub/policy.py`, `src/mcp_hub/jobs/runner.py` |

## Edge Cases & AGENTS.md Alignment

- Single-line JSON strings with all-null columns must keep column names via `_null_cols`.
- Multi-byte/oversized non-JSON text lines must slice cleanly without dropping all content to `"[... 1 more lines omitted]"`.
- `hub__status`, `hub__plan`, and `hub__batch` must stay refused inside `hub__batch` (`tests/test_batch.py`), allowing only `hub__get_full_result` when `full_results` is active.
- Every function/method kept `<= 50` lines, English-only across repo files, dependency order respected (`server` never imports `bitnet`), no private config touched, and docs synchronized for `scripts/check_docs.py`.

## Verification

- `uv run --no-sync python scripts/package_qc.py`
- User runs `.\scripts\hubctl.ps1 restart` and optional `uv run --no-sync python scripts/smoke.py` after completion.

## Open questions

- None (all design options confirmed by user).

## Progress log

- 2026-11-11 Antigravity: Created approved Option C optimization plan from session profiling and offline simulations; starting T001.
- 2026-11-11 Antigravity: Implemented T001–T008 (`src/mcp_hub/condense.py`, `full_results.py`, `batch.py`, `policy.py`, `projects.py`, `settings.py`, `server.py`, `service.py`, `bitnet/condense.py`, `jobs/runner.py`, `agents/specialists.py`, `config/hub.example.json`, `docs/`), verified replay benchmark (`zero_data: 0/22`, `lossless_inline: 9/22`, `llm_calls: 0`, `hub__plan` size 7,507 vs 17,901), and running final offline QC gate (`scripts/package_qc.py`). User will restart shared service cleanly via `scripts/hubctl.ps1 restart`.

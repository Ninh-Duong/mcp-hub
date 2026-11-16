# Implementation Plan: Source Structure Refactor

**Branch**: `refactor/source-structure` | **Date**: 2026-11-16 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/001-source-structure/spec.md`

## Summary

Restructure the `mcp_hub` Python package without changing behavior. The primary requirement is FR-001/FR-002: restore a green offline quality gate (the docs check fails on a broken AGENTS.md link). The technical approach is a sequence of behavior-preserving phases, each one commit, each gated by `scripts/package_qc.py` and `scripts/smoke.py`:

1. Capture a behavior baseline (tool list and schemas, HTTP routes, audit events, settings keys, call-path latency) **before** any code moves.
2. Fix the docs link and the Spec Kit references in AGENTS.md.
3. Add leaf utilities (`jsonio`, `clients`, `tool_meta`, `progress`, `textnorm`) and point the existing duplicates at them. Fix the layering violations.
4. Split `server.py` into `results/`, `planning/`, `child_lifecycle.py`, and smaller modules.
5. Split `jobs/runner.py` and `desktop/app.py`.
6. Move tunables into `settings.py`.
7. Consolidate test helpers into `tests/helpers/`.

## Technical Context

**Language/Version**: Python 3.12 or newer (`requires-python = ">=3.12"`, ruff target `py312`)

**Primary Dependencies**: `mcp>=2.0,<3` (MCP SDK), `starlette`, `uvicorn`, `httpx`, `PySide6-Essentials` (desktop only), `cryptography` (DPAPI-protected blobs), `tree-sitter`, and provider SDKs (`openai`, `anthropic`, `google-genai`) imported lazily

**Storage**: JSON config and state files under `config/private/` and `%LOCALAPPDATA%` (never read by agents); SQLite job store with encrypted blobs (`config/private/jobs.sqlite3`)

**Testing**: `unittest` discovery over `tests/` (1147 tests), `coverage` with a gate of 85 percent (currently 92 percent), `ruff check` for `E9,F,B,UP`, plus custom checks `check_code_standards.py`, `check_leaks.py`, `check_docs.py`. Note: `pytest` is not installed in this project; do not use it.

**Target Platform**: Windows 10/11 is primary (PowerShell scripts, DPAPI, desktop status app). `start.sh` exists for POSIX bootstrap, so the Python package must stay importable on Linux.

**Project Type**: Local MCP hub: one stdio and HTTP service that fronts child MCP servers, plus a Windows desktop status app and an optional local BitNet sidecar.

**Performance Goals**: Tool-call path latency must not regress by more than 5 percent against the baseline captured in Phase 0 (SC-007). Baseline numbers come from `scripts/bench_call_path.py`; no target numbers are invented here.

**Constraints**:
- Behavior-preserving: tool names, schemas, routes, audit event names, settings keys, and entry points stay the same (FR-010).
- No blocking synchronous work in the tool-call path (AGENTS.md).
- The desktop package imports nothing from `mcp_hub` outside `desktop/` (AGENTS.md, FR-015). Shared helpers cannot be imported by the desktop app.
- Tests run offline and must never write to `config/private/` or `logs/`.
- Tracked files are English only; no drive letters, home paths, or real emails (`check_leaks.py`).

**Scale/Scope**: 91 Python files in `src/mcp_hub`, 104 test modules, 1147 tests, 18 entries in `scripts/`. Affected hot spots: `server.py` (1357 lines), `desktop/app.py` (1183), `jobs/runner.py` (943), `policy.py` (474), `config.py` (541).

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

`.specify/memory/constitution.md` is still the unfilled template (18 placeholders). The gates below therefore come from AGENTS.md, which is the binding rule set for this repository. Running `/speckit-constitution` is optional and separate from this feature.

| Gate (source: AGENTS.md) | Status before refactor | How this plan satisfies it |
|---|---|---|
| English only in tracked files | PASS (`check_code_standards`) | All new spec, plan, research, and code text is English. |
| Functions ≤ 50 lines | PASS (`check_code_standards`) | Splits keep each function small; a QC run after each phase enforces this. |
| Every module imported by a test | PASS | New modules (`jsonio`, `clients`, `tool_meta`, `progress`, `textnorm`, `planning/*`, `results/*`, `child_lifecycle`, `jobs/tools`, `jobs/approvals`, `jobs/investigation`, `desktop/*`) each get a test in Phase 1–5. |
| Dependency direction (layering) | FAIL: `bridge→server`, `jobs/agents→server`, `results→planning`, `planning→agents`, private import `_findings_payload` | Fixed in Phase 1 (leaf modules) and Phase 2. Re-checked by a import-graph test in Phase 5. |
| Core never imports extensions | PASS | Unchanged. The new split must not add any core→`activity_ui`/`bitnet` import. |
| Desktop imports nothing outside `desktop` | PASS | Unchanged. Desktop keeps its own file I/O (see Complexity Tracking). |
| Tunables in `settings.py` | FAIL: about 15 hard-coded values in bitnet, agents, jobs, activity_ui | Phase 4. Each key keeps its current default (FR-016). |
| Docs change with the code | FAIL: AGENTS.md link broken; `check_docs` red | Phase 0, and every later phase updates its docs in the same commit. |
| One start path | PASS | Unchanged. |
| Definition of done before commit | FAIL on master: `package_qc` red | Phase 0 returns the gate to green. Each later phase must leave it green. |

**Result**: No unjustified violation blocks the plan. Every FAIL is a violation the phases below correct, and each correction is listed in the tasks. The constitution gate itself is not applicable until the constitution is filled.

## Project Structure

### Documentation (this feature)

```text
specs/001-source-structure/
├── spec.md              # Feature specification (done)
├── plan.md              # This file
├── research.md          # Phase 0 decisions
├── data-model.md        # Refactor entities and the module-boundary table
├── quickstart.md        # Runnable validation after each phase
├── contracts/           # Behavior invariants that must not change
│   └── README.md
├── checklists/
│   └── requirements.md
└── tasks.md             # Produced by /speckit-tasks (NOT created here)
```

### Source Code (repository root): target layout

Only the changed parts are listed. Unlisted modules stay where they are.

```text
src/mcp_hub/
├── jsonio.py            NEW leaf: read_json (utf-8-sig), write_json_atomic (optional mode)
├── clients.py           NEW leaf: normalize_client_name, request_client_name
├── tool_meta.py         NEW leaf: is_read_only_tool (moved from server.py)
├── progress.py          NEW leaf: forwarded _meta keys and progress relay (shared by server and bridge_proxy)
├── textnorm.py          NEW leaf: fold (merges the two identical copies in bitnet/rules.py and plan_merge.py)
├── child_lifecycle.py   NEW: child connect, lost, tools-changed handling (from server.py 182-305)
├── server.py            KEEP: HubRuntime, routing, list_tools/prompts/call_tool dispatch, create_mcp_server, main (target < 600 lines)
├── instructions.py      EXTEND: client-rules and contract text loading (from server.py)
├── batch.py             EXTEND: batch call packing (from server.py 759-869)
├── tool_pins.py         EXTEND: pin screening (from server.py 305-367); uses jsonio
├── audit.py             EXTEND: absorbs audit_events.py constants
├── hub_settings.py      uses jsonio
├── service_config.py    uses jsonio (keeps 0600 mode for the bearer-token file)
├── update_state.py      uses jsonio
├── config.py            _read_json now delegates to jsonio.read_json
├── results/             NEW subpackage (may import leaves only)
│   ├── __init__.py
│   ├── condense.py      from condense.py; fold import from textnorm
│   ├── shape.py         from result_shape.py
│   ├── full_results.py  from full_results.py + server.py 655-758, 1107-1176
│   └── cache.py         from result_cache.py
├── planning/            NEW subpackage (may import results and leaves)
│   ├── __init__.py
│   ├── hub_plan.py      server.py 518-638 (hub__plan and policy enforcement)
│   ├── policy.py        from policy.py
│   ├── playbooks.py     from playbooks.py
│   ├── confirm.py       from plan_confirm.py (MODES constant deleted)
│   └── merge.py         from plan_merge.py (roles_for_servers moves to agents/)
├── jobs/
│   ├── tools.py         NEW: tool schemas and deterministic arguments (runner.py 53-140)
│   ├── approvals.py     NEW: approval flow (runner.py 378-464)
│   ├── investigation.py NEW: investigation and specialists (runner.py 622-771)
│   └── runner.py        KEEP: lifecycle and dispatch; _settle_interrupted handled per Phase 3
├── agents/
│   └── specialists.py   receives roles_for_servers; imports is_read_only_tool from tool_meta
└── desktop/
    ├── appearance.py    NEW: theme, language, prefs (app.py 278-358)
    ├── render.py        NEW: render pipeline (app.py 359-595)
    ├── update_controller.py NEW: update flow (app.py 674-804)
    ├── stream.py        NEW: StreamController shared by usage.py and processing.py
    ├── mini.py          EXTEND: mini mode (app.py 1021-1096)
    ├── __main__.py      EXTEND: CLI and screenshot entry (app.py 1097-1183)
    └── app.py           KEEP: window assembly only

tests/
├── helpers/             NEW package: fakes.py (FakeResponse, FakeClient, FakeProtector, FakeRuntime, FakeOpener), events(), tool()
├── test_*.py            flat, renamed to match moved modules (e.g. test_planning_policy.py)
└── test_desktop_window.py  split into per-module desktop tests
```

**Structure Decision**: Keep the single-package layout and add two subpackages (`results/`, `planning/`), plus a small set of leaf modules at the top level. Subpackages are used only where a group of modules shares a real concern. Leaves stay flat because they have no concern of their own. The desktop package keeps its own structure and its own file I/O, because of the boundary rule.

## Target dependency order

The dependency order is enforced by the import-graph test added in Phase 5. Arrows point from importer to imported.

```text
leaves:   jsonio, clients, tool_meta, progress, textnorm, settings
   ↓
results:  results/*            (may import leaves; must NOT import planning)
   ↓
planning: planning/*           (may import results and leaves; must NOT import agents)
   ↓
core:     server, child_lifecycle, instructions, batch, tool_pins
   ↓
http → service                 (composition root)

extensions (activity_ui, bitnet, dashboard, jobs, agents) import core; core never imports them.
```

## Complexity Tracking

> Fill ONLY if Constitution Check has violations that must be justified

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| Desktop keeps its own JSON read and atomic write (`desktop/prefs.py`, `desktop/connection.py`, `desktop/update.py`) instead of `jsonio` | The desktop boundary rule forbids importing `mcp_hub` outside `desktop`. | Importing `jsonio` would break FR-015 and the packaging of the desktop bundle. |
| `max_chars` (output budget) stays a separate 9000 default from `condense_min_chars` (activation threshold) | They are different concepts that happen to share a value (`condense.py:202`, `bitnet/condense.py:49` vs `settings.py:134`). | Merging them would change when condensing runs or how much it keeps. |
| `_settle_interrupted` stays synchronous on the cancellation path, unless the Phase 3 test shows the async variant is safe | Awaiting inside `except asyncio.CancelledError` during shutdown can be cut short. | Blindly wrapping it in `_db` could leave a job stuck in `running`. The decision and its test are recorded in research.md. |

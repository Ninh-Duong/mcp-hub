# Track B: Desktop update experience

Status: done · Read `00-overview.md` first.

## Goal

Show whole-system update availability, progress and failures, and allow retry from the desktop app without exposing raw process output.

## Owned files

- `src/mcp_hub/desktop/update.py`, `hub_api.py`, `update_card.py`, `app.py`
- `src/mcp_hub/dashboard/locales/*.json`
- Desktop updater and UI tests

## Tasks

- [X] B001: Add HubClient calls for check/start/retry and parse the shared status model; test valid and malformed responses (files: `src/mcp_hub/desktop/hub_api.py`, desktop API tests).
- [X] B002: Add update discovery on launch and every hour plus manual check; test scheduling, offline and no-update results (files: `src/mcp_hub/desktop/app.py`, `src/mcp_hub/desktop/update.py`, `tests/test_desktop_update.py`).
- [X] B003: Render per-component progress, skipped reasons, partial completion and sanitized events while Hub is unavailable, reading only the documented update status channel (files: `src/mcp_hub/desktop/update_card.py`, `src/mcp_hub/desktop/app.py`, desktop window tests).
- [X] B004: Add retry action for failed component IDs and refresh state until completion; test failed-only retry and busy state (files: `src/mcp_hub/desktop/app.py`, `src/mcp_hub/desktop/hub_api.py`, desktop window tests).
- [X] B005: Preserve SHA-256 verification and run desktop self-installer only after Hub work; support app-only update if Hub orchestration cannot start (files: `src/mcp_hub/desktop/update.py`, `tests/test_desktop_update.py`).
- [X] B006: Add English and Vietnamese locale keys together and remove obsolete child health gate/card logic and unused `_INSTALLER` constant (files: `src/mcp_hub/dashboard/locales/en.json`, `src/mcp_hub/dashboard/locales/vi.json`, desktop updater/tests).
- [X] B007: Change the background desktop release check interval from 24 hours to one hour while retaining the startup and immediate manual checks; test the active timer and timeout wiring (files: `src/mcp_hub/desktop/app.py`, `tests/test_desktop_update.py`). Write the failing timer test first.

## Verification

- Desktop tests use fake Hub API, state documents, GitHub responses, installer and clock; no network access.
- Run `uv run python scripts/package_qc.py` and `uv run python scripts/smoke.py` at integration completion.

# Track A: Hub updater and API

Status: done · Read `00-overview.md` first.

## Goal

Implement the detached Hub-side update coordinator and its admin API using the overview's shared contract.

## Owned files

- `src/mcp_hub/update/` (new updater and state modules)
- `src/mcp_hub/dashboard/update_api.py` (new routes), `src/mcp_hub/dashboard/api.py`, `src/mcp_hub/http.py`
- `scripts/hubctl.ps1`, `src/mcp_hub/control.py`, relevant config/status plumbing
- Backend tests for updater, routes, authorization, persistence, and redaction

## Tasks

- [X] A001: Define validated update request/status models and bounded sanitized state persistence; test state transitions and corrupt/missing state (files: `src/mcp_hub/update_state.py`, `tests/test_update_state.py`).
- [X] A002: Implement effective-config selection of enabled installed children, safe Git state checks, noninteractive bounded subprocess calls, and per-repo results; test dirty/ahead/diverged/no-upstream/network cases (files: `src/mcp_hub/update_runner.py`, `tests/test_update_runner.py`).
- [X] A003: Run eligible child updates concurrently, join all outcomes, then update Hub checkout; test ordering, retries, no-op updates, and partial failures (files: `src/mcp_hub/update_runner.py`, `tests/test_update_runner.py`).
- [X] A004: Reuse existing Hub environment sync, preflight, restart, and smoke-check flow; test success and failures with mocked subprocesses (files: `scripts/hubctl.ps1`, `src/mcp_hub/update_runner.py`, `tests/test_update_runner.py`).
- [X] A005: Add admin-authenticated check/start/retry routes and launch orchestration off the event loop; reject concurrent runs and validate retry IDs (files: `src/mcp_hub/dashboard/update_api.py`, `src/mcp_hub/dashboard/api.py`, `src/mcp_hub/http.py`, `tests/test_update_api.py`).
- [X] A006: Wire the routes to the persistent status contract and document every new route/event/error (files: `src/mcp_hub/dashboard/api.py`, `docs/reference.md`, `docs/security-and-logging.md`).
- [X] A007: Make Setup reruns install a missing desktop app or refresh an older app from the latest stable Release only; reuse the existing installer and SHA-256 validation, skip current/newer installs, preserve the installed app on failures, and leave Hub/child updates to the desktop app (files: `scripts/setup.ps1`, `scripts/hubctl.ps1`, `tests/test_setup_script.py`, `tests/test_bootstrap_scripts.py`). Write failure-path tests before implementation.

## Verification

- Unit tests mock Git, network and process launches; tests do not access real checkouts or private config.
- Run `uv run python scripts/package_qc.py` and `uv run python scripts/smoke.py` at integration completion.

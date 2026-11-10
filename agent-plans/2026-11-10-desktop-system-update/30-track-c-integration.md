# Track C: Documentation and integration

Status: done · Read `00-overview.md` first.

## Goal

Align end-user and contributor docs with the shipped update behavior and run the repository's required gates.

## Tasks

- [X] C001: Update desktop and versioning docs for one-click discovery, progress, retries, Git safety, and app-last installation (files: `docs/desktop-app.md`, `docs/versioning.md`, `README.md`).
- [X] C002: Update architecture, security/logging, settings/reference docs for the new coordinator, route, persistent status and privacy limits (files: `docs/architecture.md`, `docs/security-and-logging.md`, `docs/reference.md`).
- [X] C003: Update AGENTS.md Start-here guidance and review all links/locales/doc checks (files: `AGENTS.md`, documentation touched by A/B).
- [X] C004: Run package QC and smoke; resolve failures without weakening gates (files: repository-wide as needed).
- [X] C005: Make the desktop bundle version gate read the packaged metadata instead of relying on stdout from the windowed executable; test matching, missing, and mismatched metadata (files: `scripts/build_desktop.py`, `tests/test_bootstrap_scripts.py`). Write tests first.
- [X] C006: Update Setup, desktop update cadence, and release guidance; reconcile the overview and track statuses (files: `README.md`, `AGENTS.md`, `docs/desktop-app.md`, `docs/manual-setup.md`, `docs/versioning.md`, `docs/packaging-and-qc.md`, `agent-plans/2026-11-10-desktop-system-update/`).
- [X] C007: Run package QC and smoke after A007, B007, C005, and C006; resolve failures without weakening gates (files: repository-wide as needed).
- [X] C008: After C007 passes, raise the version from 0.2.0 to the user-approved 0.3.0 in `pyproject.toml` and `uv.lock`, then rerun package QC and smoke (files: `pyproject.toml`, `uv.lock`).
- [X] C009: Isolate PyInstaller's DLL search path after the frozen-app self-check reproduced the QtCore load error; add regression coverage and verify the built installer (files: `scripts/build_desktop.py`, `tests/test_bootstrap_scripts.py`, `docs/packaging-and-qc.md`).

## Verification

- `uv run python scripts/package_qc.py`
- `uv run python scripts/smoke.py`
- Confirm `scripts/check_docs.py` and locale parity pass as part of package QC.

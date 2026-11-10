# Track C: Documentation and integration

Status: in-progress · Runs after Tracks A and B. Read `00-overview.md` first.

## Goal

Align end-user and contributor docs with the shipped update behavior and run the repository's required gates.

## Tasks

- [X] C001: Update desktop and versioning docs for one-click discovery, progress, retries, Git safety, and app-last installation (files: `docs/desktop-app.md`, `docs/versioning.md`, `README.md`).
- [X] C002: Update architecture, security/logging, settings/reference docs for the new coordinator, route, persistent status and privacy limits (files: `docs/architecture.md`, `docs/security-and-logging.md`, `docs/reference.md`).
- [X] C003: Update AGENTS.md Start-here guidance and review all links/locales/doc checks (files: `AGENTS.md`, documentation touched by A/B).
- [X] C004: Run package QC and smoke; resolve failures without weakening gates (files: repository-wide as needed).
- [ ] C005: After QC passes, ask the user whether to raise the version; do not change the version without the user's answer (files: none unless authorized).

## Verification

- `uv run python scripts/package_qc.py`
- `uv run python scripts/smoke.py`
- Confirm `scripts/check_docs.py` and locale parity pass as part of package QC.

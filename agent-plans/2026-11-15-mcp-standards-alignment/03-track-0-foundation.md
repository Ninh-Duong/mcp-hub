# Track 0: Foundation

Status: done (orchestrator, on `arch/base`)

Owns: `src/mcp_hub/settings.py`, `src/mcp_hub/audit_events.py` (new), `tests/test_settings.py`, `tests/test_audit_events.py` (new), `docs/settings.md`, `docs/security-and-logging.md`, `AGENTS.md`, `.gitignore`.

## Tasks

- [x] T001 Baseline: 953 tests passed, one skipped (before the change).
- [x] T002 `.claude/worktrees/` ignored in `.gitignore` (commit 5a3a528).
- [x] T003 `settings.py`: bool parsing (`_checked_flag`); new groups `bridge`, `protocol`, `security`; new keys in `children`, `policy`, `jobs`, `bitnet`; `_MINIMUMS` zeros and `_CHOICES` entries; `docs/settings.md` rows (commit 942473a).
- [x] T004 `audit_events.py` with four names and rows in `docs/security-and-logging.md` (commit 942473a).
- [x] T005 `AGENTS.md`: `config/private/tool_pins.json` row. The module list in the dependency line is updated in Phase 2 (T080/T081), when the modules exist.

## Deviations

- T005: the dependency-line update moved to Phase 2, because the modules it names do not exist yet.

## Progress log

- 2026-11-15 (orchestrator): `package_qc.py` passed in `../mcp-hub-arch` (coverage 91%).

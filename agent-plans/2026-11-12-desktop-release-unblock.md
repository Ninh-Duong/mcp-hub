# Unblock the desktop release so the installed app gets the BitNet row and live processing log

Status: approved

## Context
The installed status app is v0.2.0. `master` is 0.4.0 and has the BitNet row and `ProcessingLog`. The `Desktop release` workflow failed on every push to `master` since PR #3 because the unit-test step of `package_qc.py` reported 7 failures, so no installer and no `v0.4.0` tag were produced. The in-app Update card had nothing to offer.

## User stories
- **US1 (P1)** As the maintainer, a push to `master` publishes the installer, so the app can update itself. Acceptance: `Desktop release` is green; Release `v0.4.0` has the installer and its `.sha256`.
- **US2 (P2)** As the user, I can see the new app before the release is published. Acceptance: the app started from source shows the BitNet row and install button.

## Requirements
- **FR-001** `_load_children` finds sibling child repos when the checkout path is relative, short (8.3), or linked. It compares against `HUB_ROOT.parent.resolve()`.
- **FR-002** The app-refresh path computes SHA-256 with .NET (`Get-Sha256Hex`) and does not depend on `Get-FileHash`.
- **FR-003** Tests cover both fixes and the failure paths.
- **SC-001** `package_qc.py` and `smoke.py` pass locally and in CI.
- **SC-002** After Setup/Update, the installed app shows 0.4.0 with a BitNet row.

## Decisions
- Fix the product code, not only the tests.
- `0.4.0` was never released; the version question is asked before commit (`docs/versioning.md`).
- No Spec Kit scripts: the repo has no `.specify/` or constitution. `AGENTS.md` and `agent-plans/README.md` are the standard.

## Out of scope
BitNet runtime build fixes until CI shows them. `scripts/start.ps1` and the workflow's own `Get-FileHash` (runs under `pwsh`) are unchanged.

## Tasks
- [x] T001 Failing test for `_load_children` with an unresolved checkout path (`tests/test_update_runner.py`).
- [x] T002 Fix `src/mcp_hub/update_runner.py` (FR-001).
- [x] T003 Refresh tests: checksum mock targets `Get-Sha256Hex`; the success-path stub uses it too; regression test that `hubctl.ps1` has no `Get-FileHash` (`tests/test_bootstrap_scripts.py`).
- [x] T004 `Get-Sha256Hex` helper in `scripts/hubctl.ps1`, used by the refresh path.
- [x] T005 Docs check: no doc mentions the hash implementation; no change needed.
- [x] T006 `package_qc.py` passes (exit 0); `smoke.py` passes (5/5 children connected; `mcp-read-source` WARN is an unfilled account, allowed).
- [ ] T007 Ask the user about the version; commit; push; watch `gh run list --workflow desktop-release.yml`; fix the next failing step if any.
- [ ] T008 Install: rerun `Setup.cmd` or press Update in the app; verify 0.4.0.

## Verification
CI green; Release `v0.4.0` published with installer and `.sha256`; installed app reports 0.4.0 with a BitNet row; after BitNet is installed, a `hub__plan` call with only `task` adds a line to the ProcessingLog.

## Open questions
None. `0.4.0` was released; the follow-up fix ships as `0.4.1`.

## Progress log
- 2026-11-12: plan approved. T001-T006 done locally; T007 waiting for the version answer.
- 2026-11-13: `v0.4.0` released and installed. The installed app then reinstalled itself in a loop: the in-place upgrade kept `mcp_hub-0.2.0.dist-info`, so the app read 0.2.0 and offered 0.4.0, and any finished check run installed the app. Fixed in 0.4.1: `[InstallDelete]` of `{app}\_internal`, highest `mcp-hub` metadata version wins, the app installs only after a user-started Update/Retry; the update card is compacted (one summary line, three-row fading log, one main button plus a check glyph). The stale folder on an already-installed PC is removed by hand once.

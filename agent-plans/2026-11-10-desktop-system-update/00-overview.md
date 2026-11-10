# Desktop one-click system update

Status: in-progress

## Context

The desktop app currently updates only its own installer. Updating the Hub and child MCP repositories still requires commands. The goal is for a user to discover and install updates from the desktop app, see per-component progress and failures, and retry failed components without using a terminal.

## Decisions

- Check for updates at app launch and every 24 hours while open; provide a manual **Check updates** action.
- Update enabled, installed child MCP repositories concurrently, then update the Hub, run its existing dependency preparation and restart path, smoke-check, and update the desktop installer last when a newer release exists.
- Exclude disabled or missing child repositories. Never enable a child as a side effect.
- Skip a repository with a dirty worktree, local-only commits, diverged history, or no upstream tracking branch. Preserve its files and report the reason.
- Continue independent updates after a component failure. Keep status per component and retry only failed components; after retry, rerun preflight, Hub restart, and smoke check.
- Stop the Hub before modifying child checkouts to avoid Windows file locks. Run Git noninteractively with bounded timeouts.
- Persist a bounded update event/status document under user app data, containing component IDs, phase, timestamps, and sanitized error categories only. Do not store raw process output, paths, credentials, or child stderr.
- Keep first install, account setup, and AI client connection outside this feature.
- When an update starts while the Hub is stopped, the desktop app starts its existing scheduled task automatically. If that fails, report the Hub work as failed and still offer the app-only installer update.

## User stories

- US1 (P1): As a non-technical user, I can check and install all available updates from the desktop app without running commands.
- US2 (P1): As a user, I can see which components are checking, updating, complete, skipped, or failed, including while the Hub restarts.
- US3 (P1): As a user, I can retry only failed components and see the final health check.
- US4 (P2): As a user, I can update the desktop app only after Hub and child update work completes, then have the new app reopen.

## Functional requirements

- FR-001: Discover updates for the desktop release, Hub checkout, and enabled installed child checkouts.
- FR-002: Resolve child selection from the Hub's effective configuration; do not duplicate config parsing in the desktop app.
- FR-003: Reject unsafe Git updates when the worktree or branch cannot be fast-forwarded safely.
- FR-004: Update eligible children concurrently and wait for every result before continuing.
- FR-005: Update the Hub checkout, then reuse the existing locked environment, preflight, restart, and smoke-check paths.
- FR-006: Preserve progress across Hub shutdown and relaunch, and show bounded sanitized events in the app.
- FR-007: Retry selected failed components without repeating successful child pulls; rerun dependent preparation and health checks.
- FR-008: Protect update control routes with the Hub admin bearer and keep process/network work off the event loop.
- FR-009: Preserve the current SHA-256 validation and silent desktop installer flow; run it after Hub work.
- FR-010: Document user workflow, route, state/log format, errors, and settings/limits in the owning docs and all desktop locales.

## Success criteria

- SC-001: A user can complete an update cycle from the app without opening a terminal.
- SC-002: Enabled child pulls run concurrently; each result is reported independently.
- SC-003: Dirty, ahead, diverged, or untracked branches are never overwritten.
- SC-004: Progress remains visible after Hub restart and app restart.
- SC-005: Retry touches only selected failed child checkouts and reports final smoke-check results.
- SC-006: Tests prove success, failure, partial completion, retry, authorization, and redaction without network access.

## Contract to lock before parallel implementation

- Admin routes: `GET /dashboard/api/update` returns current status; `POST /dashboard/api/update/check` starts asynchronous discovery; `POST /dashboard/api/update` starts a full run; `POST /dashboard/api/update/retry` accepts failed component IDs.
- Status: a bounded local JSON state document with `run_id`, `state`, `started_at`, `updated_at`, per-component `id`, `kind`, `state`, `phase`, `error_code`, and a bounded event list. No raw stdout/stderr or paths.
- Component states: `checking`, `available`, `updating`, `complete`, `skipped`, `failed`; run states: `idle`, `running`, `partial`, `complete`.
- Update cadence default: 24 hours, as an app behavior constant unless implementation proves operator tuning is required; avoid introducing a setting without need.

## Edge cases

- Hub stopped: the app starts its existing scheduled task before requesting orchestration; if unavailable, report Hub-side work as failed and allow the desktop installer update independently.
- No Git executable, network failure, authentication unavailable, or missing upstream: report a sanitized per-component failure and continue.
- Concurrent update request: reject while a run is active and return the active run state.
- Child update failure: continue other eligible children and Hub update; retry failed child IDs only.
- Hub update failure: do not run stale preflight/restart steps against a partially changed Hub; report failure and retain retry state.
- Desktop installer failure: retain Hub/child results and allow app update retry.
- Custom Hub config: use the effective configuration; if the Hub cannot safely identify a checkout, skip with a clear error.

## Constitution check

- Architecture: desktop remains a Hub HTTP client, with the narrowly scoped persistent update-state channel documented as an exception for Hub downtime.
- Security/privacy: admin-only control; no raw command output or secret-bearing child stderr in update state.
- Event loop: subprocess, Git, disk, and network work runs in worker/background tasks.
- Tests/docs: offline tests cover success/failure; routes and behavior are documented and locale keys stay synchronized.
- Start path: reuse `hubctl`/preflight rather than adding a second dependency installation path.

## Parallel tracks

- [Track A: Hub updater and API](10-track-a-hub.md) owns orchestration, persistent status, routes, and backend tests.
- [Track B: desktop update experience](20-track-b-desktop.md) owns discovery UI, progress, retry, app installer handoff, locales, and desktop tests. Start after this overview contract is accepted; it may use fakes for the new API until Track A lands.
- [Track C: docs and integration](30-track-c-integration.md) follows A and B because it records the final route and status contract.

## Requirements analysis

| Requirement | Tasks |
|---|---|
| FR-001–FR-005 | A001–A004, B001–B003 |
| FR-006–FR-008 | A001, A005–A006, B003–B004 |
| FR-009 | B005 |
| FR-010 | C001–C003 |

## Open questions

- None. The repository does not contain `.specify/` or its setup script; this plan follows the repo-required `agent-plans/` Spec Kit format.

## Progress log

- 2026-11-10 root: implemented Hub/API and desktop tracks in parallel, updated docs and passed package QC; smoke passed with no enabled children. Remaining: ask the user whether to raise the version, then close the plan.

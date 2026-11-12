# BitNet Processing Log

Status: done

## Context

The desktop Status app shows whether the BitNet sidecar is ready, but it does not show what the Hub is doing with it. Add a compact live processing log beneath the BitNet row so an operator can see the main orchestration stages without viewing prompts, tool arguments, results, or model output.

This plan follows the repository's Spec Kit format under `agent-plans/` because this checkout has no `.specify/` project setup. The existing BitNet plan covers the original switch/runtime and does not cover this feature.

## User stories

- **US1 (P1):** As an operator, I can see whether BitNet is ready and follow planning, child-tool calls, result condensation, and local summary stages in the Status app.
  - **Acceptance:** A BitNet-enabled Hub emits visible start/outcome events. Events identify whether planning or condensation used BitNet, rules, or fallback. Child call events may identify the configured server and tool.
- **US2 (P1):** As an operator, I can follow the log without enabling Track AI clients.
  - **Acceptance:** The processing stream starts whenever the Hub reports BitNet enabled, regardless of `activity_ui`; disabling BitNet or stopping/restarting the Hub clears stale rows.
- **US3 (P1):** As an operator, I can read a compact, private timeline.
  - **Acceptance:** At most three rows are visible, newest first. The prior rows move down and become muted. No prompt/task/question text, arguments, results, model output, AI-client identity, paths, URLs, or arbitrary exception text is emitted or stored.

## Functional requirements

- **FR-001:** Define a core-neutral, typed processing-event contract. Core runtime and job orchestration may report events only through this contract and never import BitNet.
- **FR-002:** The BitNet extension owns a bounded in-memory event history and subscriber queues. It emits an initial snapshot, ordered live events, and a bounded resync snapshot when a subscriber falls behind.
- **FR-003:** Expose the stream through `GET /dashboard/api/bitnet/processing/stream` only when the BitNet extension is installed. Use existing dashboard authentication; the MCP client token remains denied.
- **FR-004:** Emit events at direct planning, direct and job child calls, condensation, job planning, and local draft summarization. Event fields are a fixed allowlist of sequence, timestamp, operation code, state, source, optional configured server/tool IDs, bounded duration, and fixed error category.
- **FR-005:** The desktop uses a worker thread and the existing Qt result queue. Widgets change only on the Qt thread; reconnect uses bounded backoff and snapshot sequence IDs to avoid duplicate rows.
- **FR-006:** Show a maximum of three newest-first rows under the BitNet status row. Store event codes and localize them in English and Vietnamese. Clear the log when BitNet becomes disabled, the Hub stops/restarts, the app closes, or an obsolete stream generation reports data.
- **FR-007:** With BitNet disabled or no stream route available, existing Hub behavior, status fields, Activity UI, child usage rows, and mini bar continue unchanged.
- **FR-008:** Keep events transient in process memory and do not add persistent storage, new settings, audit payloads, or a dependency on Activity UI.

## Success criteria

- **SC-001:** Planning, tool-call, condensation, job-plan, and local-summary activity appears in the Status app through the authenticated live stream.
- **SC-002:** A burst of events leaves exactly the newest three visible in order, with older rows muted; theme and language changes preserve correct presentation.
- **SC-003:** Tests prove that BitNet-off mode and Activity UI on/off combinations preserve existing behavior and that no content-bearing fields reach SSE.
- **SC-004:** `uv run python scripts/package_qc.py` and `uv run python scripts/smoke.py` pass.

## Architecture contract

- Core declares a frozen `ProcessingEvent` data type and a `ProcessingObserver` protocol in `extensions.py`. The event uses fixed operation/state/source/error codes and optional allowlisted `server_id`/`tool_name`; it has no free-form message or payload field.
- The BitNet extension implements the protocol with a bounded history of the latest three events and bounded per-subscriber queues. Subscriber overflow replaces queued deltas with a fresh snapshot of the latest three events.
- Active BitNet setup serves `GET /dashboard/api/bitnet/processing/stream` as SSE. `snapshot` data is `{stream_id, sequence, events:[...]}` in oldest-to-newest order. `event` data is one event. SSE IDs are `<stream_id>:<sequence>`. Initial and overflow-resync messages both use `snapshot`.
- Event fields are `stream_id`, `sequence`, `at` (UTC ISO timestamp), `operation`, `state`, plus optional `source`, `server_id`, `tool_name`, `duration_ms`, and `error_code`. Operations are `plan`, `child_call`, `condense`, `job_plan`, and `local_summary`; states are `started`, `succeeded`, `failed`, and `fallback`; sources are `bitnet`, `rules`, `child`, `cache`, `fallback`, and `local`; error codes are `timeout`, `unavailable`, `cancelled`, `tool_error`, and `unexpected`. No additional keys are serialized.
- Use dashboard's existing authentication middleware; admin bearer and paired session are allowed, while the MCP client token remains denied.
- The desktop worker consumes snapshot and event messages, forwards them through the existing app queue, deduplicates by stream ID/sequence, and never touches Qt widgets. A changed stream ID clears rows from the previous Hub process.
- Activity UI `CallObserver`, its SQLite history, audit JSONL, and child stderr files are not used as the processing-log store.

## Decisions

- The visible cap is three rows; newest is at the top and old entries fade by row position using native Qt opacity. Use native Qt animations for the downward transition if layout behavior stays stable; do not embed browser/CSS rendering in the desktop app.
- Log content is fixed localized text derived from event codes. Server/tool IDs may be included because they are configured Hub metadata; never include client labels or call data.
- Events are memory-only and reset on Hub restart. The desktop reconnect snapshot may repopulate the latest three events from the current Hub process.
- The stream endpoint is read-only and uses existing admin-or-paired-session dashboard access. No settings, schema migration, version bump, or release action is part of this feature.
- Implement in a separate `codex/bitnet-processing-log` worktree. Backend and desktop implementation tracks own disjoint files; a third agent performs a read-only integrated regression review. Do not commit, push, merge, or alter the version unless the user asks.

## Out of scope

- Showing prompts, task/question content, tool arguments/results, model output, AI-client identity, job findings, or arbitrary error messages.
- Replacing or changing Activity UI history, audit logging, the BitNet sidecar log, or dashboard activity pages.
- Persistent history, filtering/search, configurable row count, or changes to BitNet behavior and inference decisions.
- Installer, package, version, release, and child-MCP changes.

## Tasks

- [x] **T001 Backend event contract/feed (tests first):** add the typed contract and bounded in-memory feed, including sequence, snapshot, queue overflow/resync, and safe-field projection. Files: `src/mcp_hub/extensions.py`, `src/mcp_hub/bitnet/processing.py`, `tests/test_processing_feed.py`.
- [x] **T002 Backend instrumentation (tests first):** inject the optional observer into runtime and job runner; emit bounded events for plan assistance, child calls, condensation, job planning, and local summarization. Files: `src/mcp_hub/server.py`, `src/mcp_hub/jobs/runner.py`, `src/mcp_hub/bitnet/__init__.py`, matching processing tests.
- [x] **T003 Stream/API wiring (tests first):** add the SSE route to the active BitNet extension, ensure teardown unsubscribes, test auth and disabled/old-Hub behavior, and document the contract. Files: `src/mcp_hub/bitnet/setup.py`, BitNet/API tests, `docs/bitnet.md`, `docs/architecture.md`, `docs/security-and-logging.md`, `docs/reference.md`.
- [x] **T004 Desktop stream client (tests first):** parse snapshot/delta/resync messages, bound reconnect delay, forward through the Qt queue, and reject stale generations. Files: `src/mcp_hub/desktop/processing.py`, `tests/test_desktop_processing.py`.
- [x] **T005 Desktop log UI (tests first):** show three localized rows under BitNet, animate/mute older rows, wire stream lifecycle independently of Activity UI, and clear on lifecycle changes. Files: `src/mcp_hub/desktop/app.py`, `src/mcp_hub/desktop/widgets.py` if needed, `src/mcp_hub/dashboard/locales/en.json`, `src/mcp_hub/dashboard/locales/vi.json`, desktop tests, `docs/desktop-app.md`.
- [x] **T006 Integrated review:** an independent agent reviews the assembled diff against every requirement and checks BitNet-off, Activity UI on/off, failure/reconnect, older Hub, locale/theme, and mini-bar regressions; resolve findings in the owning track.
- [x] **T007 Final verification:** run targeted tests, `uv run python scripts/package_qc.py`, and `uv run python scripts/smoke.py`; record outcomes below.

## Traceability matrix

| Requirement | Tasks | Verification |
|---|---|---|
| FR-001 | T001, T002 | Contract unit tests; core imports remain extension-neutral |
| FR-002 | T001, T003 | Ring cap/order/sequence, overflow resync, disconnect cleanup |
| FR-003 | T003 | Admin/session success; no-token/client-token denial; disabled route behavior |
| FR-004 | T002 | Direct plan/call/condense and job plan/call/summary event tests |
| FR-005 | T004 | Parser, reconnect/backoff, dedupe, stale generation, stop tests |
| FR-006 | T005 | Three rows, order/fade, locale, restart/disable/close lifecycle tests |
| FR-007 | T002, T003, T005, T006 | BitNet-off and Activity UI on/off regression matrix |
| FR-008 | T001, T003, T006 | No content fields, no disk writes, bounded memory checks |
| SC-001 | T002–T005 | End-to-end stream and desktop integration tests |
| SC-002 | T005 | Window UI tests for bursts, ordering, and opacity |
| SC-003 | T001–T006 | Privacy assertions and cross-feature regression review |
| SC-004 | T007 | Full package QC and smoke outputs |

## Edge cases and AGENTS.md check

- Subscriber queue fills during a burst: discard stale deltas and send a snapshot/resync marker; memory remains bounded.
- Hub restarts while the desktop worker is blocked: stop/generation token drops queued messages; reconnect receives the new stream ID.
- Route is absent on an older Hub or BitNet is disabled: fail quietly, clear the log, and retain current status display.
- Malformed/unknown event: ignore it without throwing, changing the footer, or clearing valid newer entries.
- Concurrent jobs interleave events: preserve feed sequence order; do not expose job IDs, role prompts, or findings.
- Keep functions at most 50 lines, English-only source/docs, locale strings in both locale files, all I/O off the event loop, settings unchanged, tests offline and outside private/log directories, architecture dependency direction intact, and docs synchronized.

## Progress log

- 2026-11-12 Codex: Created the Spec Kit-style plan from the approved design; baseline QC passed (802 tests, one skipped; coverage 91%); implementation starts with T001/T004 in parallel after this contract is frozen.
- 2026-11-12 Integration review: Caught and resolved 3 integration items test-first: (1) desktop handling of truncated SSE response stream with reconnect, (2) backend 15-second content-free heartbeat comments to prevent idle timeouts, and (3) resilient `ProcessingFeed` allowed-routes extraction in `install` when runtime has minimal mock/duck attributes.
- 2026-11-12 Final verification: `package_qc.py` passed with 831 tests (0 failures, 1 skipped), 91% coverage (exceeding 85% requirement), leak check PASS, code standards PASS, docs check PASS, compile check PASS. `smoke.py` passed. All success criteria met.

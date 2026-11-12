# Track 1: Backend event contract, producers, and SSE

Status: done

Read `00-overview.md` and follow its architecture contract. Own only the files listed below. Do not edit desktop UI/client files or plan files. Use TDD: write each test first, run it and confirm the expected failure, then implement the minimal change.

## Owns

- `src/mcp_hub/extensions.py`
- `src/mcp_hub/server.py`
- `src/mcp_hub/jobs/runner.py`
- `src/mcp_hub/bitnet/__init__.py`
- `src/mcp_hub/bitnet/processing.py` (new)
- `src/mcp_hub/bitnet/setup.py`
- `tests/test_processing_feed.py` (new)
- `tests/test_processing_api.py` (new)
- focused event assertions in existing server/job tests as needed
- `docs/bitnet.md`, `docs/architecture.md`, `docs/security-and-logging.md`, `docs/reference.md`

## Contract to implement

- Frozen typed event with fixed codes: operation (`plan`, `child_call`, `condense`, `job_plan`, `local_summary`), state (`started`, `succeeded`, `failed`, `fallback`), source (`bitnet`, `rules`, `child`, `cache`, `fallback`, `local`), optional bounded duration/error category, and optional configured server/tool names. Never accept free-form message/payload dictionaries.
- Optional `ProcessingObserver` on HubRuntime/JobRunner. It is `None` by default. Emission performs only bounded in-memory work and cannot change operation result or policy.
- BitNet feed has bounded event history and bounded subscriber queues. Events have a stream ID and monotonically increasing sequence. Initial subscription sends a snapshot of the latest three; overflow collapses queued deltas into a resync notification whose snapshot recovers the latest three.
- Active BitNet setup serves `GET /dashboard/api/bitnet/processing/stream` as SSE. Use dashboard's existing authentication middleware; do not add auth bypasses or persist events. Closing the response unsubscribes.
- Emit around direct plan assistance and job planning; each routed child call; attempted/result condensation; and local draft summarization. Source/outcome must accurately distinguish model use from rules and fallback. No prompt, question, task, arguments, result, output, client label, exception text, or job ID.

## Tasks

- [x] T001 Add failing tests for event codes, safe projection, ring order/cap, sequence, queue overflow/resync, and subscriber cleanup; implement the event contract/feed.
- [x] T002 Add failing tests for direct planning/call/condensation and job planning/call/summary events, including BitNet-off no-op; inject and emit through the optional observer.
- [x] T003 Add failing tests for SSE snapshot/live events, disconnect cleanup, auth (admin and paired session allowed; client token and no token denied), and route absence when BitNet is off; wire the route.
- [x] T004 Update backend-owned docs and confirm reference/runtime docs checks pass.
- [x] T005 Fix reviewer P2 idle-stream behavior: add a failing heartbeat test, then a bounded SSE comment heartbeat preserving event sequences, history, and subscriber cleanup.

## Acceptance

- Existing code paths are unchanged when `processing_observer is None`.
- All event construction is typed and bounded; tests serialize events containing sentinel secrets/content and prove those values are absent.
- No `mcp_hub.bitnet` imports are added to core modules; service remains the extension installation boundary.
- Add no settings, database writes, audit payloads, or file writes.

## Progress log

- 2026-11-12 Codex: Contract frozen in overview; backend track begins after baseline QC.

- 2026-11-12 Codex: Implemented typed processing events, bounded feed and authenticated SSE, runtime/job instrumentation, privacy/error/auth tests, and backend docs with red-green cycles. Focused suite passed 121 tests; ruff and code standards passed. Coordinator owns the docs checker extension-route enumeration fix and final QC/smoke.

- 2026-11-12 Codex: Resolved reviewer P2 with a 15-second content-free idle SSE heartbeat after reproducing the missing heartbeat with a failing test. API/feed/setup focused suite passed 26 tests; ruff and code standards passed. Event/feed contract is unchanged; final integrated QC remains with the coordinator.

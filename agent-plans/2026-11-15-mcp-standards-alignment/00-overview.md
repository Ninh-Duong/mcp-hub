# MCP standards alignment (plan v3)

Status: in-progress

Approved by the user on 2026-11-15 after three review rounds. Each agent reads this overview and only its own track file, and changes only the files its track owns.

## Context

The Hub is a single-user loopback gateway: it fronts five stdio child MCP servers and serves AI clients over Streamable HTTP, plus a stdio bridge. A review against the MCP 2026-07-28 spec, the SDK in use (`mcp` 2.2.0), and the common gateways (FastMCP, Docker MCP Gateway, mcp-proxy, ContextForge, agentgateway) found these gaps, which this plan fixes:

- Condensing rewrites a result to text only. It drops `structuredContent`, image and resource blocks, while the child's `outputSchema` is still advertised (spec MUST violation). See `src/mcp_hub/server.py` `_finish_condensed` and `_maybe_condense`.
- No `notifications/tools/list_changed` is sent when routes change, and the bridge drops the list-changed events it receives.
- The stdio bridge replaces the Hub's instructions with its own one-liner and does not forward progress.
- `with_plan_id` adds the parameter only to root `properties`, which breaks schemas that use `allOf`, `anyOf`, `oneOf` or `$ref`.
- No tool-definition pinning (rug-pull or poisoning goes unnoticed); no argument validation; no description screening.
- `FullResultStore` is bounded by entry count only.
- Investigation specialists keep every tool result in context; no budget; prompt caching unused; some limits are hard-coded.

A previous plan that split `HubRuntime` and added rate limiting, sandboxing, extras and POSIX support was judged too risky for this Hub. Those items are in **Deferred** below.

## Decisions (fixed; do not reopen)

- D1. Results that carry `structuredContent`, non-text blocks, or belong to a tool with an `outputSchema` pass through unchanged. Condensing is only for other read-only text results.
- D2. `HubRuntime` and `jobs/runner.py` are not split in this plan.
- D3. Pinning, argument validation and description screening run in `warn` mode only. There is no block mode and no new UI in this plan.
- D4. `_ERROR_PREFIXES` (text-based failure detection), `confirmPlaybook=always`, and the repeat-refusal default stay as they are.
- D5. No rate limiting, child sandbox, extras split, POSIX support, telemetry exporter, or Tasks extension in this plan (see Deferred).
- D6. One event bus: `HubEvents` (`src/mcp_hub/events.py`). `activity_ui` reuses its listen handler instead of registering another.
- D7. Implementation agents never review their own branch with a skill. The orchestrator reviews each branch before merge.
- D8. Only the orchestrator merges, and only into `arch/base`. Merging into `master`, pushing, and raising the version need the user's explicit approval.
- D9 (amendment, Phase 2). The Hub always serves `subscriptions/listen` through `HubEvents.listen_handler`, with or without the activity UI (FR-005). Two existing tests assert the old absence and are the only test changes allowed by this decision: `tests/test_extensions.py` `ActivityUiTest.test_attach_server_serves_presence_listen` (its assertion that the listen is unanswered before `attach_server` changes to answered) and `tests/test_bridge.py` `PresenceTest.test_presence_returns_against_a_hub_without_the_activity_ui` (it now waits for a change instead of returning). Keep each test's purpose; change only the assertions that encode the old absence. No other existing test changes.
- D10 (amendment, Phase 2 cross-track wiring). Track W may edit these files, and only for the items below, with tests: `src/mcp_hub/jobs/runner.py`, `src/mcp_hub/agents/specialists.py`, `src/mcp_hub/agents/providers.py` (pass `jobs.specialistMaxRounds`, `jobs.toolResultChars`, `jobs.coordinatorSeconds` and `jobs.promptCache` through the constructors, keeping the module-level aliases that existing tests import; drop `COORDINATOR_SECONDS` only if nothing reads it after the change); `src/mcp_hub/events.py` (add an optional `on_publish` callback, default `None`, called once per publish, so the `tools.list_changed.sent` audit line is written per publish, matching `docs/security-and-logging.md`); `src/mcp_hub/tool_pins.py` (`PinStore.snapshot()` copies the data on the calling thread, `save` writes the snapshot in a worker thread, a save error is logged at WARNING with the exception type only). Existing tests change only where they assert the old behaviour of these items.
- Accepted as documented limitations (no code change in this plan): 2025 clients receive no Hub-side list change (the SDK drops standalone notifications on legacy sessions); `$ref` closed definitions still refuse `hub_plan_id` (Track D); prompt-cache savings unverified live; a corrupt pin file is treated as empty.

## User stories (priority)

- US1 (P1) Clients receive results that match the spec: a result with structured content or non-text blocks is never rewritten.
- US2 (P1) Clients learn when the tool list changes (child reconnect, child-sent change, a child turned on or off), including clients behind the stdio bridge.
- US3 (P1) Stdio clients receive the same instructions and progress as HTTP clients.
- US4 (P2) The Hub notices when a child changes a tool definition (audit line and `hub__status`).
- US5 (P2) The Hub starts serving without waiting for a slow child; a late child's tools appear later through `list_changed`.
- US6 (P2) Specialists run inside a context budget and use the provider's prompt cache.
- US7 (P2) Tests prove the Hub serves both protocol eras (2025 handshake and 2026-07-28 modern).

## Functional requirements

- FR-001 Do not condense when the result has `structuredContent`, a non-text block, or the tool declares an `outputSchema`.
- FR-002 A condensed result carries `_meta["mcp-hub/condensed"]` = `{result_id, kept, total}`; the existing footer stays.
- FR-003 `hub__get_full_result` returns `isError=true` for bad input; it never raises.
- FR-004 `FullResultStore` is bounded by total bytes; the oldest entries go first.
- FR-005 The Hub always answers `subscriptions/listen` through one bus (`HubEvents`); `activity_ui` reuses it.
- FR-006 When the tool set or a tool definition changes, exactly one `ToolsListChanged` is published per debounce window.
- FR-007 The Hub listens to each child's `tools/list_changed` and relists that child's tools without reconnecting.
- FR-008 `tools/list` has a stable order (Hub tools, then server id, then tool name) and carries a cache hint.
- FR-009 The bridge passes the Hub's instructions through, relays progress, and relays `tools/list_changed` to stdio clients.
- FR-010 The Hub passes `progress_callback` and `_meta` (minus connection-owned keys) to the child.
- FR-011 `with_plan_id` yields a valid schema for roots using `allOf`, `anyOf`, `oneOf` or `$ref`.
- FR-012 Argument validation against the child's input schema runs in `warn` mode: audit only, the call still runs.
- FR-013 Pin each child tool by SHA-256 over canonical JSON of name, description, inputSchema, outputSchema and annotations; a changed hash writes `tool.definition.changed` and shows in `hub__status`; the pin file stores hashes only.
- FR-014 Screen descriptions for hidden Unicode characters and excess length; write `tool.description.suspicious`.
- FR-015 The Hub starts serving after `children.startupWaitSeconds` or once every child has finished its first attempt, whichever comes first.
- FR-016 Plan ids are 128-bit. `policy.repeatAfterSeconds` lets an identical successful call repeat after N seconds (default 0 = refused, as today).
- FR-017 A specialist's context is bounded by `jobs.contextChars`: older tool results become one-line evidence references; every `source_id` survives.
- FR-018 Prompt caching is enabled by `jobs.promptCache`; the stable prefix is ordered tools first and carries no timestamps.
- FR-019 `coordinatorSeconds`, `specialistMaxRounds` and `toolResultChars` come from `settings.jobs`.
- FR-020 Server instructions are at most 4096 characters; essentials come first.
- FR-021 In-memory conformance tests cover the 2025 handshake and the 2026-07-28 modern path.

## Success criteria

- SC-001 Every existing test passes unchanged, except tests a track card names explicitly.
- SC-002 A test checks every condensed or passed-through result against its `outputSchema` with `jsonschema`.
- SC-003 A fake child that reconnects with a new tool causes exactly one client notification.
- SC-004 The Hub is ready within 12 seconds while one fake child hangs.
- SC-005 Coverage stays at or above 85%; `scripts/package_qc.py` passes after every merge; no lint rule is suppressed.
- SC-006 A specialist transcript never exceeds `jobs.contextChars` in a test with a fake provider.

## Edge cases

- A child that reconnects repeatedly: the debounce keeps notifications to one per window.
- Clients on the 2025 handshake receive `notifications/tools/list_changed` through the session; 2026 clients through `subscriptions/listen`.
- A child that updates itself and changes a hash: warn mode audits it; the tool keeps running.
- A child whose schema is wrong: validation audits and never blocks.
- `BRIDGE_TIMEOUT` is used by `hub_http_client` and tests: keep the name as the default value.
- Two `subscriptions/listen` handlers would overwrite each other: `ActivityUi` must wrap `HubEvents.listen_handler`, never register its own.

## Must stay the same

- Tool names `<prefix>__<tool>`; `hub_plan_id` stays optional; `hub__plan` and `confirm_token` schemas.
- `confirmPlaybook=always`.
- The condensed footer text and `result_id`; the batch item headers; the `Policy:` wording; `_ERROR_PREFIXES`.
- Existing audit event names (new ones are only added).
- `/mcp`, `/health`, the `mcp-hub-bridge` command.

## Rules that apply to every track

- **Settings:** JSON and docs keys are camelCase; dataclass fields are snake_case; zero must appear in `_MINIMUMS`; text choices in `_CHOICES`. `docs/settings.md` rows are checked both ways by `scripts/check_docs.py`.
- **Audit events:** a new event name is a constant in `src/mcp_hub/audit_events.py` and a row in `docs/security-and-logging.md`. The event rows for this plan already exist (added in Phase 0).
- **New modules:** each new `src/mcp_hub/*.py` file is imported by at least one test. Tests live flat in `tests/` (there is no `tests/__init__.py`).
- **Functions:** at most 50 lines; one job each; English identifiers, comments, logs and docs (`scripts/check_code_standards.py` rejects accented letters in source).
- **Offline tests:** no network; wait with timeouts; never write into `config/private/` or `logs/`.
- **Docs change with code:** update the doc that owns the topic in the same commit.
- **Plans:** the leak check and code-standards check apply to this folder. Do not write drive letters, user names, or machine paths here. `check_docs.py` does not read this folder.

## Phases and gates

```
Phase 0  Foundation   DONE on arch/base (commits 942473a, 5a3a528)
Phase 1  Modules      tracks A-F in parallel, each in its own worktree and branch arch/track-<x>
                      gate per track: its own tests + ruff + check_code_standards pass
                      orchestrator: review the diff, merge into arch/base, run package_qc
Phase 2  Wiring       track W, one agent, edits server.py and the other shared files
                      gate: package_qc + smoke in the arch checkout (see 02-orchestrator.md)
Phase 3  Quality      track Q: conformance tests for both eras, ruff families B and UP
Final    orchestrator runs package_qc, the smoke check, and the code review skill; asks the user about merge and version
```

Files shared by tracks are owned by one track each. A track that needs a change in another track's file writes a request in its track card's progress log instead of editing it.

## Haiku protocol (every implementation agent)

Start every session with `git status`, `git log --oneline -10`, then read this overview and your track card.

Loop for each task:
1. Read only the files the task names.
2. Write the test first. Run it and see it **fail**: `uv run --no-sync python -m unittest tests.<module> -v`
3. Implement the signature exactly as the track card gives it. Keep functions at 50 lines or fewer. Copy the style of the example file the card names.
4. Run the test and see it **pass**. Never edit or delete an existing test to make it pass.
5. `uv run --no-sync python -m ruff check .` then `uv run --no-sync python scripts/check_code_standards.py`.
6. Tick the task in your track card, add one line to its progress log, and commit. One task, one commit. Commit message ends with the attribution line the orchestrator gives you.

Before reporting done, run `uv run --no-sync python scripts/package_qc.py` in your worktree. Report: task IDs done, files changed, the **actual output** of the verify commands, deviations from the card, and blockers. A "done" report without output is rejected.

Stop and report (do not guess) when:
- a verify command fails twice in a row;
- a needed change is in a file another track owns;
- a signature, name or line reference in the card does not match the code (for example, after the file changed);
- lint would need suppressing, or coverage would drop;
- the task needs a design decision the card does not state.

Use `uv run --no-sync` everywhere. Shell commands in the cards start from the Hub checkout root (the folder that contains `agent-plans/`); your worktree is the sibling `../mcp-hub-track-<x>`, so each command begins with `cd "../mcp-hub-track-<x>"`. Never push. Never merge. Never change `version` in `pyproject.toml`.

## Phase 1 track cards

| Track | Card | Owns (new files marked *) |
|---|---|---|
| A results | `04-track-a-results.md` | `result_shape.py`*, `full_results.py`, tests |
| B events | `05-track-b-events.md` | `events.py`*, `children.py`, `fake_stdio_child.py`, tests |
| C bridge | `06-track-c-bridge.md` | `bridge.py`, `bridge_proxy.py`*, tests |
| D schema and policy | `07-track-d-schema-policy.md` | `schema_rewrite.py`*, `policy.py`, tests |
| E pins | `08-track-e-pins.md` | `tool_pins.py`*, tests |
| F harness | `09-track-f-harness.md` | `instructions.py`*, `agents/`, `jobs/runner.py`, tests |

Phase 2 is `10-track-w-wiring.md`; Phase 3 is `11-track-q-quality.md`.

## Task analysis (requirement to task)

| FR | Tasks |
|---|---|
| FR-001, 002 | A-T10, A-T11, W-T70 |
| FR-003 | A-T12, W-T71 |
| FR-004 | A-T13 |
| FR-005, 006 | B-T20, W-T72, W-T73 |
| FR-007 | B-T21, W-T74 |
| FR-008 | W-T75 |
| FR-009 | C-T30 to C-T34 |
| FR-010 | W-T76 |
| FR-011 | D-T40, D-T41 |
| FR-012 | D-T42, W-T79 |
| FR-013, 014 | E-T50 to E-T52, W-T78 |
| FR-015 | W-T77 |
| FR-016 | D-T43, D-T44 |
| FR-017, 018, 019 | F-T61 to F-T64 |
| FR-020 | F-T60, W-T80 |
| FR-021 | Q-T90 |

## Deferred (each would be its own plan)

- Splitting `HubRuntime` and `jobs/runner.py`.
- Extras split, headless job, POSIX support, `hubctl.sh`.
- Block mode for pins, and a dashboard accept route.
- Structured results condensed with `_meta` only (FastMCP's approach).
- Removing text-based failure detection (`_ERROR_PREFIXES`).
- Refcount-based cancellation in `ResultCache`.
- Rate limits, per-child concurrency limits, circuit breaker.
- Child sandbox (Job Object) and ping-based health checks.
- OpenTelemetry exporter, metrics endpoint, readiness route.
- Tasks extension (SDK 2.2.0 has no API for it).
- Eval suite for the harness (20-50 recorded incidents).
- Tool search and `sideEffect` or `useHubPython` config keys.
- Per-owner plan binding (all clients share one bearer token).

## Progress log

- 2026-11-15 (orchestrator): Phase 0 committed on `arch/base`: settings keys with tests, `audit_events.py` with tests, docs rows, AGENTS.md private-file row, and `.claude/worktrees/` ignored. Baseline before the change: 953 tests passed, one skipped.
- 2026-11-15 (orchestrator): Phase 1 merged into `arch/base` (tracks A, B, C, D, E, F). Decisions taken during Phase 1: `FullResultStore.put` returns `None` only for an entry larger than `max_bytes`; the bridge keeps its own process-local `subscriptions/listen` bus (`events.py` is the Hub's bus, D6 concerns the Hub process). `package_qc.py` on the merge: 1069 tests passed, one skipped; coverage 92%.
- Phase 2 merged into `arch/base` (track W, with D9 and D10). `package_qc.py`: 1129 tests passed, one skipped; coverage 92%. The end-to-end smoke check is not run yet: starting a second Hub service replaces the connection file the user's running Hub uses, so it needs the user's go-ahead or an isolated run.
- Known limits accepted for this plan (see D10): 2025 clients receive no Hub-side list change; a `$ref` root whose definition is closed still refuses `hub_plan_id`; prompt-cache savings are unverified live; a corrupt pin file is treated as empty.

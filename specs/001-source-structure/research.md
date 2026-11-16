# Phase 0 Research: Source Structure Refactor

All items were resolved from the repository itself. No external research was needed, and the spec contained no `[NEEDS CLARIFICATION]` markers.

## R1. Capture a behavior baseline before moving any code

- **Decision**: Before Phase 1, record a baseline of the public surface and the call-path latency on the refactor branch. The baseline covers the MCP tool list (names, descriptions, input schemas), the HTTP route table, the audit event names, the settings keys with their defaults, and the latency from `scripts/bench_call_path.py`.
- **Rationale**: SC-006 and SC-007 compare "before" and "after". Without a recorded baseline these criteria cannot be checked.
- **How**: Build the MCP server with `create_mcp_server` against the fake child in `tests/fake_stdio_child.py`, so no real credentials or network are used. Dump the result as sorted JSON. Read the route table from `create_http_app`. Read audit names from `audit_events.py` and `docs/security-and-logging.md`. Store the snapshot under `tests/contract_baseline/`, guarded by `tests/test_surface_contract.py` (the plan first named `specs/`; moved so the check keeps running). It must contain no values from `config/private/` (AGENTS.md).
- **Alternatives considered**: Compare only by reading code. Rejected, because it cannot prove that schemas are identical.

## R2. Merge the two `fold` copies into `textnorm.py`

- **Decision**: Create a leaf module `textnorm.py` with one `fold`. Point `bitnet/rules.py` and `plan_merge.py` (moving to `planning/merge.py`) at it. Point `condense.py` (moving to `results/condense.py`) at it too.
- **Rationale**: The two functions are equivalent. Both replace `U+0111` with `d`. `bitnet/rules.py` replaces `U+0110` with `d`, while `plan_merge.py` replaces it with `D`. Both finish with `.casefold()`, which turns `D` into `d`, so the output is identical. The duplicate is also the only reason `condense.py` depends on `plan_merge`.
- **Verification**: A unit test checks `fold` on `U+0110`, `U+0111`, a mixed-case Vietnamese phrase, and an empty string, and asserts the output is the same as the old copies. Write the test input with `\u` escapes so the source stays ASCII (`check_code_standards` forbids Vietnamese characters in tracked files).
- **Alternatives considered**: Keep both copies and only fix the import direction. Rejected, because a duplicate with a different replacement table invites future drift.

## R3. One JSON helper module, with a mode option

- **Decision**: Add `jsonio.py` with `read_json(path)` (UTF-8 with BOM accepted, `utf-8-sig`) and `write_json_atomic(path, value, *, mode=None)`. Atomic means: write a temporary file in the same directory, optionally `chmod` it, then `os.replace`.
- **Rationale**: The current code mixes two readers. `config._read_json`, `hub_settings`, `child_requirements`, and `extensions` use `utf-8-sig`. `projects`, `service_config`, `update_state`, `tool_pins`, `agents/config`, and `bitnet/sidecar` use plain `utf-8`. `utf-8-sig` accepts every file that `utf-8` accepts, so unifying on it only widens the accepted input. Writers differ too: `service_config` uses `NamedTemporaryFile` and sets mode `0600` on the bearer-token file. `update_state` and `tool_pins` use `mkstemp` or a named temp. The helper must keep the `0600` mode for `service_config`.
- **Scope**: Desktop modules (`desktop/prefs.py`, `desktop/connection.py`, `desktop/update.py`, `desktop/hub_api.py`) keep their own I/O, because of the desktop boundary (see plan.md Complexity Tracking). `bitnet/installer.py` writes downloads and only uses `jsonio` for its state file if that file is JSON.
- **Behavior note**: The encoding change is deliberate and is reported in the release notes. It only affects files with a BOM, which currently raise an error in the `utf-8` readers.
- **Alternatives considered**: Leave the readers alone and document the difference. Rejected: it is the duplicated code the spec asks to remove, and the BOM case is a real Windows-editor problem.

## R4. `is_read_only_tool` goes to `tool_meta.py`, not `tool_pins.py`

- **Decision**: New leaf `tool_meta.py` holds `is_read_only_tool`, moved from `server.py:1205`.
- **Rationale**: `tool_pins.py` stores hashes of tool definitions. `is_read_only_tool` reads the MCP annotations of one tool. They are different concerns. The helper is used by `server.py:398`, `jobs/policy.py`, `jobs/runner.py`, and `agents/specialists.py`.
- **Alternatives considered**: Put it into `tool_pins.py`, as the earlier plan suggested. Rejected: it would make the pin module depend on a concern it does not own.

## R5. Child lifecycle gets its own module, not `children.py`

- **Decision**: Move `server.py` lines 182–305 (connect, child lost, tools changed, announcements, background tasks) into a new `child_lifecycle.py`. Leave `children.py` as the process supervisor.
- **Rationale**: `children.py` (264 lines) is process supervision. The moved code is about how the runtime reacts to a child's state. Merging the two would grow a module that already has a clear purpose, and would not fix the layering.
- **Size check**: After the moves listed in plan.md, the estimate for `server.py` is about 480 lines, under the 600-line target in SC-002. Confirm with `wc -l` after Phase 2.

## R6. Progress forwarding has one implementation in `progress.py`

- **Decision**: New leaf `progress.py` holds the forwarded `_meta` key set and the progress relay. `server.py` and `bridge_proxy.py` both import it.
- **Rationale**: The key set is defined twice: `server.py:1025` lists the protocol keys by hand, and `bridge_proxy.py:28` builds them from SDK constants. The send-progress closures are identical. Using the SDK constants in both places keeps the set in step with the SDK.
- **Verification**: Existing tests for progress and `_meta` forwarding must stay green. Add one test that asserts both paths forward the same key set.

## R7. Settings: move tunables, but do not merge different concepts

- **Decision**: Move the hard-coded values listed in the review into `settings.py`, each with the same default. Keep two concepts separate:
  - `condense_min_chars` (`settings.py:134`, the activation threshold) is used by `agents/specialists.py:193` and `jobs/runner.py:183`. These are the same concept and point at one key.
  - `max_chars` (the output budget) in `condense.py:202` and `bitnet/condense.py:49` is a different concept. It gets its own key, for example `rule_condense_max_chars`, with default 9000. It is not merged with `condense_min_chars`.
- **Rationale**: The two values only share a number. Merging them would change when condensing runs or how much it keeps.
- **Verification**: Add a test that the default of each new key equals the previous hard-coded value. Document every key in `docs/settings.md` (checked by `check_docs.py`).
- **Alternatives considered**: One key for all 9000 values. Rejected because of the semantic difference above.

## R8. `_settle_interrupted` on the cancellation path

- **Decision**: In Phase 3, run `_settle_interrupted` through a worker thread, and add a test that cancelling a running job still ends in state `interrupted`. If that test cannot be made reliable, keep the call synchronous and record it as an accepted exception in Complexity Tracking.
- **Rationale**: `jobs/runner.py:429` and `:529` call the synchronous SQLite method inside `except asyncio.CancelledError`. AGENTS.md forbids blocking work on the event loop in the tool-call path. This method runs on the job path, and wrapping it in `_db` inside a cancellation handler can be cut short by a second cancellation during shutdown. The test decides the approach, not a guess.
- **Alternatives considered**: Wrap in `_db` without a test. Rejected: it risks leaving jobs stuck in `running`.

## R9. Test helpers and cross-test imports

- **Decision**: Create `tests/helpers/` (package with `__init__.py`) holding `fakes.py`. Move the fakes used by several test modules there. Rewrite the nine modules that import from other tests. Keep test modules flat, so `check_code_standards` keeps mapping modules to tests the way it does now.
- **Rationale**: The fakes are copied: `FakeResponse` in four files, `FakeClient` in six, `events()` in seven. The nine cross-imports (`tests.test_desktop`, `tests.test_job_store`, `tests.test_dashboard_api`, `tests.test_desktop_usage`, `tests.test_desktop_processing`) would break if those test files were renamed or split.
- **Alternatives considered**: `conftest.py`. Rejected: the project uses `unittest`, not `pytest` (`pytest` is not installed).

## R10. Import-graph test instead of a new lint tool

- **Decision**: Add a `unittest` test that parses the imports of `src/mcp_hub` with `ast` and asserts the target dependency order in plan.md. It also asserts that `bridge.py` does not import `server`, that `desktop` imports nothing outside itself, and that no module imports a name starting with `_` from another module.
- **Rationale**: No new dependency is needed, and the test runs in the existing QC.
- **Alternatives considered**: Add `import-linter`. Rejected: it adds a dependency that the project's policy does not already allow.

## R11. Phase ordering and commits

- **Decision**: One commit per phase on `refactor/source-structure`. Each commit must leave `scripts/package_qc.py` green and pass `scripts/smoke.py`. No version bump is made during the refactor. Version changes require the maintainer's approval (`docs/versioning.md`).
- **Rationale**: A failed phase can be reverted alone. The version question is asked after the work is finished, as AGENTS.md requires.

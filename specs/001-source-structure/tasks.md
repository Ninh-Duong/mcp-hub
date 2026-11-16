# Tasks: Source Structure Refactor

**Input**: Design documents from `specs/001-source-structure/`
**Prerequisites**: plan.md (required), spec.md (required for user stories), research.md, data-model.md, contracts/, quickstart.md

**Tests**: Included. FR-019 requires a test for every new or moved module and for every new helper's success and failure paths, and AGENTS.md requires it too. Tests are therefore requested for this feature, not optional.

**Organization**: Tasks are grouped by user story (spec.md, priorities P1–P3) so each story can be verified on its own. Each story phase ends with one commit on `refactor/source-structure` (FR-021), gated by `scripts/package_qc.py` and the surface contract test.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependency on an unfinished task)
- **[Story]**: US1–US6, mapped to spec.md user stories
- Paths are repository-relative. Use `src/mcp_hub/` for package code.

## Notes for the implementer

- **Line numbers are from the review at HEAD `1d48a8c`.** Earlier moves shift them. Re-locate each block by its function name before moving it.
- **Behavior-preserving**: a phase whose surface contract test fails is not committed. Change the code back, or stop and report the change as a behavior change.
- **Plan deviations recorded here** (also reflected in `contracts/README.md` by T007):
  - The surface baseline lives in `tests/contract_baseline/` and is guarded by `tests/test_surface_contract.py`, so the check keeps running after the refactor. The original plan put it under `specs/`.
  - `bootstrap.py` is an additional small module for the shared config/settings bootstrap (T046). The plan named the helper but not its file.
  - The boundary test checks the explicit rules from data-model.md. It does not forbid `preflight → children`, which is not a rule.
- **Installer write** (`bitnet/installer.py:261`) writes `install.json` through a fixed `install.json.tmp` name. Switching it to `jsonio` uses a unique temp name. The behavior is the same, and the change is noted in the commit message.
- **No desktop helper sharing**: `desktop/` never imports `jsonio` or any other `mcp_hub` module (FR-015).

---

## Phase 1: Setup

**Purpose**: Confirm the starting point and commit the Spec Kit artifacts.

- [X] T001 Verify the branch with `git branch --show-current` (must print `refactor/source-structure`) and confirm `master` is unchanged in `.git` (no commit made on master)
- [X] T002 Commit the Spec Kit files `.specify/` and `specs/001-source-structure/` as a single commit: "docs(spec): add Spec Kit artifacts for source structure refactor"

---

## Phase 2: Foundational (blocking prerequisites)

**Purpose**: Record the behavior baseline and the dependency-rule guard. These must exist before any source file moves.

**CRITICAL**: No user story phase starts until this phase is complete.

- [X] T003 Create `tests/surface_capture.py` with one function per surface: MCP tools (name, description, inputSchema, built by `create_mcp_server` against `tests/fake_stdio_child.py`), HTTP routes (`http.create_http_app`), audit event names (`audit_events.py`), settings keys and defaults (`settings.py`), and entry points (`pyproject.toml` `[project.scripts]` plus the `-m` modules used in `scripts/*.ps1`). The module must not read `config/private/` or `logs/`, and must patch `config.PRIVATE_ROOT` and the audit path (AGENTS.md test rules).
- [X] T004 Generate the baseline JSON files `tests/contract_baseline/tools.json`, `routes.json`, `audit_events.json`, `settings.json`, `entry_points.json` from `tests/surface_capture.py`, sorted and indented. Review them by eye for secrets or machine paths before committing (`scripts/check_leaks.py` also scans them).
- [X] T005 Create `tests/test_surface_contract.py`: for each baseline file, build the current surface and assert equality. The comparison is exact. An intended change (for example new settings keys in Phase 7) regenerates the baseline with `python -m tests.surface_capture` in the same commit, and the commit message explains it.
- [X] T006 [P] Record the latency baseline: run `scripts/bench_call_path.py` three times on the current branch and write the median numbers (no hostname or path) to `tests/contract_baseline/bench.json`. Mark the file as manual-only (not run by unit tests).
- [X] T007 [P] Update `specs/001-source-structure/contracts/README.md` and `specs/001-source-structure/quickstart.md` so they point to `tests/contract_baseline/` and `tests/test_surface_contract.py` instead of `contracts/baseline/`.
- [X] T008 Create `tests/test_module_boundaries.py`: parse the imports of `src/mcp_hub` with `ast` and enforce the rules in data-model.md (bridge must not import server; jobs and agents must not import server; results must not import planning, agents, or server; planning must not import agents or server; core must not import activity_ui, bitnet, dashboard, jobs, or agents; desktop imports nothing from `mcp_hub` outside `desktop`; no import of an underscore-prefixed name from another module). Put the current violations in a `KNOWN_VIOLATIONS` set. US2 must empty that set.
- [X] T009 Run `uv run --no-sync python scripts/package_qc.py` and confirm it reports only the known docs failure (AGENTS.md link). Record the result in the commit message of Phase 2.
- [X] T010 Commit Phase 2: "test: capture behavior baseline and module boundary guard"

**Checkpoint**: Baseline and guard exist. Every later phase is checked against them.

---

## Phase 3: User Story 1 - Restore a green quality gate (Priority: P1) 🎯 MVP

**Goal**: `package_qc.py` passes on the refactor branch (spec FR-001, FR-002, FR-003).

**Independent Test**: `uv run --no-sync python scripts/package_qc.py` exits 0; `tests/test_check_docs.py` passes.

- [X] T011 [US1] Replace the broken link on AGENTS.md line 21 (to `agent-plans/README.md`) with a link to `specs/` and the `/speckit-*` commands in `AGENTS.md`
- [X] T012 [US1] Update the "Start here" table row "Plan a feature, or implement one planned earlier" and the "Before you change anything" bullet about `agent-plans/` in `AGENTS.md` to describe the Spec Kit flow (`/speckit-specify` → `/speckit-plan` → `/speckit-tasks` → `/speckit-implement`)
- [X] T013 [P] [US1] Find every remaining reference to `agent-plans` outside `docs/history/` with a search of tracked files, and update each one in its own doc (`README.md`, `docs/` files) to point to `specs/`
- [X] T014 [US1] Run `uv run --no-sync python scripts/check_docs.py` and `uv run --no-sync python -m unittest tests.test_check_docs -v`; both must pass
- [X] T015 [US1] Run `uv run --no-sync python scripts/package_qc.py` in full; it must exit 0. Then run `uv run --no-sync python -m unittest tests.test_surface_contract -v` and confirm no difference
- [X] T016 [US1] Commit Phase 3: "docs: point AGENTS.md at Spec Kit specs; fix broken plan link"

**Checkpoint**: QC is green on the refactor branch. The MVP gate is in place.

---

## Phase 4: User Story 2 - Clean dependency direction and shared helpers (Priority: P1)

**Goal**: Fix every layering violation in `KNOWN_VIOLATIONS`, and make each duplicated helper exist once (FR-004 to FR-008 scope for helpers; FR-006; FR-007).

**Independent Test**: `tests/test_module_boundaries.py` passes with an empty `KNOWN_VIOLATIONS`; `bridge` import does not load `mcp_hub.server`; `fold`, JSON read, and atomic write each have one definition used by all callers.

### Leaf modules (new, with tests)

- [X] T017 [P] [US2] Create `src/mcp_hub/jsonio.py` with `read_json(path)` (encoding `utf-8-sig`) and `write_json_atomic(path, value, *, indent=None, separators=None, mode=None)`: write a temporary file in the target folder, `chmod` it when `mode` is set, `os.replace` it, and delete the temporary file on failure. Each function stays under 50 lines.
- [X] T018 [P] [US2] Create `tests/test_jsonio.py`: a UTF-8 file with BOM reads correctly; invalid JSON raises `json.JSONDecodeError`; a failure in `json.dumps` leaves the old file unchanged; `mode=0o600` is applied to the written file; `indent` and `separators` are honored.
- [X] T019 [P] [US2] Create `src/mcp_hub/textnorm.py` with `fold(text)` copied from `src/mcp_hub/plan_merge.py:12`. Keep the `\u` escapes so the file stays ASCII.
- [X] T020 [P] [US2] Create `tests/test_textnorm.py`: check `fold` on `\u0110`, `\u0111`, a mixed-case Vietnamese phrase written with escapes, and an empty string, and assert the same outputs as the two legacy copies (`bitnet/rules.py:18` and `plan_merge.py:12`).
- [X] T021 [P] [US2] Create `src/mcp_hub/clients.py` with `normalize_client_name` (from `src/mcp_hub/server.py:86`) and `request_client_name` (from `src/mcp_hub/server.py:94`), moved unchanged.
- [X] T022 [P] [US2] Create `tests/test_clients.py`: normalization of empty, long, and unicode names; `request_client_name` returns `unknown` when the context has no client info.
- [X] T023 [P] [US2] Create `src/mcp_hub/tool_meta.py` with `is_read_only_tool` moved from `src/mcp_hub/server.py:1205`.
- [X] T024 [P] [US2] Create `tests/test_tool_meta.py`: a read-only annotated tool returns True; a tool without annotations returns False; a tool with `readOnlyHint=False` returns False.
- [X] T025 [P] [US2] Create `src/mcp_hub/progress.py` with the forwarded `_meta` key set and the progress relay. Use the SDK constants as in `src/mcp_hub/bridge_proxy.py:28`, and remove the hand-written copy in `src/mcp_hub/server.py:1025`.
- [X] T026 [P] [US2] Create `tests/test_progress.py`: assert that the key set the server forwards equals the key set the bridge forwards; the progress relay sends one notification per progress update.

### Rewire callers (fix the layering)

- [X] T027 [US2] Point `src/mcp_hub/bridge.py:21` at `clients.normalize_client_name` and remove its import from `server`
- [X] T028 [US2] Point `src/mcp_hub/activity_ui/client_sessions.py:11` at `clients.request_client_name`; remove the server import from `src/mcp_hub/server.py` for both helpers (keep the definitions only in `clients.py`)
- [X] T029 [US2] Add a test to `tests/test_bridge.py` that imports `mcp_hub.bridge` in a fresh subprocess and asserts `mcp_hub.server` is not in `sys.modules`
- [X] T030 [US2] Point `src/mcp_hub/jobs/policy.py:5`, `src/mcp_hub/jobs/runner.py:29`, `src/mcp_hub/agents/specialists.py:17`, and `src/mcp_hub/server.py:398` at `tool_meta.is_read_only_tool`; remove the `server` imports from `jobs/` and `agents/`
- [X] T031 [US2] Point `src/mcp_hub/bitnet/rules.py:18` and `src/mcp_hub/plan_merge.py:12` at `textnorm.fold`; delete their local copies
- [X] T032 [US2] Point `src/mcp_hub/condense.py:10` at `textnorm.fold` instead of `plan_merge`, so `condense` no longer depends on planning
- [X] T033 [US2] Move `roles_for_servers` from `src/mcp_hub/plan_merge.py:88` into `src/mcp_hub/agents/specialists.py`; remove the lazy import at `plan_merge.py:90`; update the caller at `src/mcp_hub/jobs/runner.py:580`
- [X] T034 [US2] Rename `_findings_payload` to `findings_payload` in `src/mcp_hub/agents/coordinator.py:18` and its callers (`coordinator.py:48`, `src/mcp_hub/jobs/runner.py:15` and `:630`); update the test that imports it
- [X] T035 [US2] Delete `MODES` from `src/mcp_hub/plan_confirm.py:22` (no references); confirm `src/mcp_hub/settings.py` holds the only list of `confirm_playbook` values

### Replace duplicated JSON readers and writers

- [X] T036 [US2] Make `src/mcp_hub/config.py:145` `_read_json` delegate to `jsonio.read_json`, keeping its error messages
- [X] T037 [P] [US2] Replace the reader in `src/mcp_hub/hub_settings.py:106` with `jsonio.read_json`
- [X] T038 [P] [US2] Replace the readers in `src/mcp_hub/child_requirements.py:72`, `src/mcp_hub/projects.py:68`, `src/mcp_hub/service_config.py:58` and `:130`, `src/mcp_hub/update_state.py:30`, `src/mcp_hub/tool_pins.py:70`, `src/mcp_hub/agents/config.py:45`, `src/mcp_hub/bitnet/sidecar.py:22` with `jsonio.read_json`. Leave `extensions.py:224` alone: it reads text lines, not JSON.
- [X] T039 [US2] Replace the atomic writer in `src/mcp_hub/hub_settings.py:122` with `jsonio.write_json_atomic`, keeping its formatting
- [X] T040 [US2] Replace the atomic writer in `src/mcp_hub/tool_pins.py:109-123` with `jsonio.write_json_atomic`
- [X] T041 [US2] Replace the atomic writer in `src/mcp_hub/update_state.py:60-64` with `jsonio.write_json_atomic` using the same compact separators
- [X] T042 [US2] Replace the atomic writer in `src/mcp_hub/service_config.py:140-160` with `jsonio.write_json_atomic(..., mode=0o600)`; add a test that the written file's mode is `0600` on POSIX (skip on Windows, where `chmod` has no effect)
- [X] T043 [US2] Replace the writer in `src/mcp_hub/bitnet/installer.py:250-261` (`write_install`) with `jsonio.write_json_atomic(..., indent=2)`; note the unique temp name in the commit message
- [X] T044 [US2] Do NOT change `src/mcp_hub/desktop/prefs.py`, `desktop/connection.py`, `desktop/update.py`, or `desktop/hub_api.py` (desktop boundary; plan.md Complexity Tracking)

### Tests follow the moved symbols

- [X] T045 [US2] Update every test that imports a moved or renamed symbol (`server.is_read_only_tool`, `server.normalize_client_name`, `plan_merge.fold`, `bitnet.rules.fold`, `plan_confirm.MODES`, `plan_merge.roles_for_servers`, `_findings_payload`) so the test imports the new location; find them with a search of `tests/`
- [X] T046 [US2] Empty `KNOWN_VIOLATIONS` in `tests/test_module_boundaries.py`; the test must pass without the set

### Gate

- [X] T047 [US2] Run `uv run --no-sync python scripts/package_qc.py`, then `uv run --no-sync python -m unittest tests.test_surface_contract -v`; both must pass with no surface difference
- [X] T118 [US2] Make the private names used across modules public: rename `_ERROR_MARKER_RE` to `ERROR_MARKER_RE` in `src/mcp_hub/condense.py` and update `src/mcp_hub/bitnet/condense.py`; rename `_read_install` to `read_install` in `src/mcp_hub/bitnet/sidecar.py` and update `src/mcp_hub/bitnet/setup.py` (FR-006; the boundary test lists both)
- [X] T119 [US2] Remove `jobs.runner -> agents.coordinator._findings_payload` and the two bitnet entries from `KNOWN_VIOLATIONS` in `tests/test_module_boundaries.py` once T034 and T118 are done; the boundary test must still pass
- [X] T048 [US2] Commit Phase 4: "refactor(core): add leaf helpers and fix dependency direction"

**Checkpoint**: Layering rules are enforced by a test. Helpers exist once.

---

## Phase 5: User Story 3 - Split the central server module (Priority: P2)

**Goal**: `server.py` holds only runtime, routing, dispatch, and entry points, and is under 600 lines (FR-009, FR-010; SC-002).

**Independent Test**: `wc -l src/mcp_hub/server.py` is under 600; `tests/test_surface_contract.py` passes with no difference; `scripts/smoke.py` passes when children are configured.

### Results subpackage

- [X] T049 [US3] Create `src/mcp_hub/results/__init__.py` and move `src/mcp_hub/condense.py` → `results/condense.py`, `result_shape.py` → `results/shape.py`, `result_cache.py` → `results/cache.py`, `full_results.py` → `results/full_results.py`. Update every import in `src/`, `tests/`, `scripts/` (including `scripts/bench_call_path.py` and `scripts/check_docs.py`).
- [X] T050 [US3] Move the full-result and condense helpers from `src/mcp_hub/server.py:655-758` (`_get_full_result`, `_maybe_condense`, `_finish_condensed`, `_full_result_tool`) and the page formatting at `server.py:1107-1176` into `results/full_results.py`

### Planning subpackage

- [X] T051 [US3] Create `src/mcp_hub/planning/__init__.py` and move `src/mcp_hub/policy.py` → `planning/policy.py`, `playbooks.py` → `planning/playbooks.py`, `plan_confirm.py` → `planning/confirm.py`, `plan_merge.py` → `planning/merge.py`. Update every import, including `scripts/check_docs.py` and tests.
- [X] T052 [US3] Create `src/mcp_hub/planning/hub_plan.py` from `src/mcp_hub/server.py:518-638` (`_assist_plan`, `_clean_hints`, `_create_plan`, `_confirm_gate`, and the policy enforcement and audit)

### Child lifecycle and other server parts

- [X] T053 [US3] Create `src/mcp_hub/child_lifecycle.py` from `src/mcp_hub/server.py:182-305` (connect, child lost, tools changed, announcements, background tasks). Keep the same `HubRuntime` behavior; the functions take the runtime as an argument.
- [X] T054 [US3] Create `tests/test_child_lifecycle.py`: connect registers tools; a lost child removes its tools and emits the announcement; a tools-changed event refreshes the list.
- [X] T055 [US3] Move the tool-description screening and pin saving from `src/mcp_hub/server.py:305-367` into `src/mcp_hub/tool_pins.py`
- [X] T056 [US3] Move the batch packing from `src/mcp_hub/server.py:759-869` (`_call_batch`, `_pack_batch_items`, `_pack_condense`, `_batch_policy`) into `src/mcp_hub/batch.py`
- [X] T057 [US3] Move the contract and instruction reading from `src/mcp_hub/server.py:1076-1105` and `:1231-1290` into `src/mcp_hub/instructions.py`; keep one definition of the `docs/` folder path, shared with `planning/playbooks.py`
- [X] T058 [US3] Replace `getattr(runtime, "settings"/"config"/"condenser", …)` in `src/mcp_hub/service.py:51,88,118` and `src/mcp_hub/server.py:77-80` with typed attributes declared on `HubRuntime`
- [X] T059 [US3] Create `src/mcp_hub/bootstrap.py` with the shared config and settings load used by both `service.py` and `server.serve()` (`server.py:1292-1356`), and make both call it
- [X] T060 [US3] Create `tests/test_bootstrap.py`: the helper loads defaults when no config file exists, and raises the same error as before for a broken config file

### Size and boundary checks

- [X] T061 [US3] Confirm `src/mcp_hub/server.py` is under 600 lines with `wc -l`; if not, move the next largest block and repeat
- [X] T062 [US3] Update `tests/test_module_boundaries.py` to cover `results/`, `planning/`, `child_lifecycle`, and `bootstrap`; the test must still pass
- [X] T063 [US3] Update the existing `tests/test_server_*.py` files that import moved helpers, so each one imports from the new module

### Docs (same commit, FR-020)

- [X] T064 [US3] Update `docs/architecture.md` (module list, dependency chain, the new `results/`, `planning/`, `child_lifecycle`, `bootstrap`, `clients`, `jsonio`, `tool_meta`, `progress`, `textnorm`) and the "Keep the architecture" line in `AGENTS.md`
- [X] T065 [P] [US3] Update `docs/reference.md` and any other doc that lists module paths (`grep` for `server.py`, `plan_merge`, `result_cache`, `full_results`, `policy.py` outside `docs/history/`)
- [X] T066 [P] [US3] Document the UTF-8 BOM acceptance for config files in `docs/architecture.md` (the readers now all accept BOM)

### Gate

- [X] T067 [US3] Run `uv run --no-sync python scripts/package_qc.py` and `uv run --no-sync python -m unittest tests.test_surface_contract -v` (no difference allowed)
- [X] T068 [US3] Run `uv run --no-sync python scripts/bench_call_path.py`; the median must be no more than 5 percent above `tests/contract_baseline/bench.json` (SC-007)
- [X] T069 [US3] Commit Phase 5: "refactor(server): split server into results, planning, and child lifecycle modules"

**Checkpoint**: `server.py` is a thin runtime and entry point. Routing and surface are unchanged.

---

## Phase 6: User Story 4 - Split the job runner and the desktop app (Priority: P2)

**Goal**: `jobs/runner.py` and `desktop/app.py` each hold one concern per module; no function over 50 lines; the synchronous settle call is fixed (FR-011 to FR-015; FR-012).

**Independent Test**: Job and desktop tests pass; `desktop/` imports only `desktop`; manual desktop check from quickstart section 6.

### Job runner

- [X] T070 [US4] Create `tests/test_job_settle.py` first: a job whose task is cancelled during a running write ends in state `interrupted`, and the test fails if the settle step is skipped (write it before the change, expect it to pass on current code, then keep it as the guard)
- [X] T071 [US4] Create `src/mcp_hub/jobs/tools.py` from `src/mcp_hub/jobs/runner.py:53-140` (`_build_deterministic_args`, `_ENV`, `_JOB_ID`, `_JOB_TOOLS`, `_object_schema`, `_limit_text`, `create_provider`). Split `_build_deterministic_args` (57 lines) into `_logs_args` and `_sql_args` so each function is under 50 lines.
- [X] T072 [P] [US4] Create `src/mcp_hub/jobs/approvals.py` from `src/mcp_hub/jobs/runner.py:378-464` (the approval flow) and `tests/test_job_approvals.py` covering approve, reject, wrong code, and an expired approval
- [X] T073 [P] [US4] Create `src/mcp_hub/jobs/investigation.py` from `src/mcp_hub/jobs/runner.py:622-771` (investigation and specialist steps) and a test file for the moved steps
- [X] T074 [US4] Fix `_settle_interrupted` at `src/mcp_hub/jobs/runner.py:429` and `:529` per research.md R8: run it through a worker thread inside the `except asyncio.CancelledError` branch, then confirm `tests/test_job_settle.py` still passes. If the test shows that is unsafe, keep the call synchronous, add a note in plan.md Complexity Tracking, and stop this task
- [X] T075 [US4] Confirm `src/mcp_hub/jobs/runner.py` is under 500 lines and no function in `jobs/` is over 50 lines (`scripts/check_code_standards.py` enforces the second)

### Desktop app

- [X] T076 [P] [US4] Create `src/mcp_hub/desktop/stream.py` with `StreamController`, extracted from the reconnect loop in `src/mcp_hub/desktop/usage.py:146-186` and `src/mcp_hub/desktop/processing.py:126-165`; keep each module's parser function in place
- [X] T077 [US4] Switch `src/mcp_hub/desktop/usage.py` and `src/mcp_hub/desktop/processing.py` to `StreamController`; `tests/test_desktop_usage.py` and `tests/test_desktop_processing.py` must pass unchanged
- [X] T078 [P] [US4] Create `src/mcp_hub/desktop/appearance.py` from `src/mcp_hub/desktop/app.py:278-358` (theme, language, saved preferences)
- [X] T079 [P] [US4] Create `src/mcp_hub/desktop/render.py` from `src/mcp_hub/desktop/app.py:359-595` (render pipeline for children, clients, usage, BitNet)
- [X] T080 [P] [US4] Create `src/mcp_hub/desktop/update_controller.py` from `src/mcp_hub/desktop/app.py:674-804` (update flow)
- [X] T081 [US4] Move mini mode from `src/mcp_hub/desktop/app.py:1021-1096` into `src/mcp_hub/desktop/mini.py`
- [X] T082 [US4] Move the command line and screenshot code from `src/mcp_hub/desktop/app.py:1097-1183` into `src/mcp_hub/desktop/__main__.py`; keep `app.main` importable, because `packaging/desktop/entry.py` imports it (re-export or update the import in that file)
- [X] T083 [US4] Use `constants.APP_DATA_DIR` in `src/mcp_hub/desktop/hub_api.py:142` instead of the literal `"mcp-hub-status"`
- [X] T084 [US4] Confirm `src/mcp_hub/desktop/app.py` holds only window assembly and is under 400 lines; no function in `desktop/` is over 50 lines

### Gate and docs

- [X] T085 [US4] Update `docs/desktop-app.md` (module list and what each new module does), and `docs/architecture.md` and `docs/operations/shared-agent-hub.md` for the `jobs/` split
- [X] T086 [US4] Run `uv run --no-sync python scripts/package_qc.py` and the surface contract test (no difference)
- [ ] T087 [US4] Run `uv run --no-sync python scripts/smoke.py` on the maintainer's machine (needs child configuration; report which children were skipped)
- [ ] T088 [US4] Desktop manual check per quickstart section 6: status view, update card, mini mode, usage and processing views, and window close (the Hub keeps running)
- [X] T089 [US4] Commit Phase 6: "refactor(jobs,desktop): split job runner and desktop app into focused modules"

**Checkpoint**: Each job and desktop concern is in its own module. The settle path is covered by a test.

---

## Phase 7: User Story 5 - Move tunables into settings (Priority: P3)

**Goal**: Every tunable in the review is a documented settings key with its current default; the 9000 default is defined in one place per concept (FR-016, FR-017; SC-008).

**Independent Test**: `tests/test_settings_defaults.py` passes; `scripts/check_docs.py` passes; the surface contract shows only added keys.

- [X] T090 [US5] Add `ActivitySettings` to `src/mcp_hub/settings.py` with `batch_size=50`, `batch_seconds=1.0`, `queue_limit=10000`, `stream_keepalive_seconds=15.0`, and add it to `Settings`
- [X] T091 [US5] Pass `ActivitySettings` values into `src/mcp_hub/activity_ui/recorder.py:11-12` and `src/mcp_hub/activity_ui/routes.py:16` through their constructors, and wire them in `src/mcp_hub/service.py`; no module reads the config itself
- [X] T092 [US5] Add the BitNet keys to `BitnetSettings` in `src/mcp_hub/settings.py`: sidecar context size `4096` (`bitnet/sidecar.py:101`), health timeouts `0.5` and `1.0` (`sidecar.py:111`, `:156`), summary input limit `10000`, summary output `400`, summary timeout `30.0` (`bitnet/summarize.py:22-24`), setup wait `5.0` (`bitnet/setup.py:171`), and chunk size `10000` (`bitnet/condense.py:27`, `:95`)
- [X] T093 [US5] Put the coordinator and provider values in `JobSettings` (not a new `AgentSettings`, see research.md R7): coordinator input `12000`, output `1200`, summary `4000` (`agents/coordinator.py`), provider timeout `60.0` and output `2048` (`agents/providers.py`); pass them from `JobRunner` limits (`jobs/investigation.py`, `jobs/tools.py`)
- [X] T094 [US5] Add to `JobSettings` in `src/mcp_hub/settings.py`: detail page default limit `2000` (`jobs/runner.py:254`) and question max length `5000` (`jobs/runner.py:279`); read them from the settings object passed at start
- [X] T095 [US5] Make `agents/specialists.py:193` and `jobs/runner.py:183` take `condense_min_chars` from the existing `settings.py:134` value passed in at start (same concept, one key)
- [X] T096 [US5] Do NOT add `rule_condense_max_chars`. Set the constructor defaults of `RuleCondenser` (`results/condense.py`) and `BitnetCondenser` (`bitnet/condense.py`) to `BitnetSettings().condense_min_chars` and `BitnetSettings().condense_timeout_seconds`, so the 9000 literal exists once, in `settings.py` (research.md R7)
- [X] T097 [US5] Create `tests/test_settings_defaults.py`: a table of every moved key and its old literal default; each key's default must equal that literal
- [X] T098 [US5] Document every new key in `docs/settings.md` with its default and meaning; `scripts/check_docs.py` must pass
- [X] T099 [US5] Add a comment at `src/mcp_hub/desktop/system.py:23` (`DRAIN_WAIT_SECONDS = 40`) naming `HttpSettings.shutdown_drain_seconds`, because the desktop cannot import `settings.py`
- [X] T100 [US5] Run `uv run --no-sync python scripts/package_qc.py` and the surface contract test; the only allowed difference is the new settings keys, with `tests/contract_baseline/settings.json` regenerated in that same commit
- [X] T101 [US5] Commit Phase 7: "refactor(settings): move hard-coded tunables into settings with unchanged defaults"

**Checkpoint**: No tunable from the review remains as a literal in code.

---

## Phase 8: User Story 6 - Share test fakes (Priority: P3)

**Goal**: Tests import fakes from one helper package; no test imports from another test module; no fake is copied (FR-018; SC-005).

**Independent Test**: `grep` finds no `from tests.test_` import and no duplicate fake class; `package_qc.py` passes.

- [X] T102 [US6] Create `tests/helpers/__init__.py` and `tests/helpers/fakes.py` with `FakeResponse`, `FakeClient`, `FakeProtector`, `FakeRuntime`, `FakeOpener`, `events()`, and `tool()`, copied from the first test file that defines each one (list the candidates by searching `tests/` for `class Fake` and `def events`)
- [X] T103 [P] [US6] Replace the cross-test imports in the nine modules that import from other tests: `tests.test_desktop` (TOKEN, FakeOpener), `tests.test_job_store` (FakeProtector), `tests.test_dashboard_api` (FakeRuntime), `tests.test_desktop_usage` (FakeResponse, FakeStop), `tests.test_desktop_processing` (snapshot, event) with imports from `tests.helpers`. Keep `snapshot` and `event` helpers in `tests/helpers/` if more than one module uses them.
- [X] T104 [US6] Delete the copied fake definitions in the test files that now import from `tests.helpers` (`FakeResponse` ×4, `FakeClient`/`_FakeClient` ×6, `events()` ×7, `tool()` ×5, `runtime()` ×4)
- [X] T105 [US6] Split `tests/test_desktop_window.py` (1579 lines) into one file per desktop module: `test_desktop_window_render.py`, `test_desktop_window_update.py`, `test_desktop_window_mini.py`, `test_desktop_window_appearance.py`, and `test_desktop_window_cli.py`. Each file keeps the same tests; no test is removed.
- [ ] T106 [P] [US6] Rename test files whose module moved, for clarity, for example `tests/test_plan_merge.py` → `tests/test_planning_merge.py`, `tests/test_policy.py` → `tests/test_planning_policy.py`; `check_code_standards` maps tests by import, so the rename does not break the module check
- [X] T107 [US6] Add a search-based test in `tests/test_module_boundaries.py` (or a new `tests/test_test_hygiene.py`) that fails if any test module imports from another `tests.test_*` module
- [X] T108 [US6] Run `uv run --no-sync python scripts/package_qc.py` and confirm the test count is the same as before the phase (no test removed)
- [X] T109 [US6] Commit Phase 8: "test: share fakes via tests/helpers and split the desktop window tests"

**Checkpoint**: Tests are organized by module, and fakes are defined once.

---

## Final Phase: Polish & cross-cutting concerns

**Purpose**: Verify the whole feature end to end.

- [X] T110 [P] Run `uv run --no-sync python scripts/package_qc.py` on the final commit; coverage must be at least 85 percent
- [X] T111 [P] Check SC-002 and SC-003: `wc -l` shows no `src/mcp_hub` file over 600 lines except those already listed as under review; no function over 50 lines (`scripts/check_code_standards.py`)
- [X] T112 [P] Run the surface contract test; confirm no difference except the settings keys added in Phase 7
- [X] T113 Run `uv run --no-sync python scripts/bench_call_path.py` and compare with `tests/contract_baseline/bench.json` (SC-007)
- [X] T114 Search tracked files outside `docs/history/` for old module paths and removed names (`agent-plans`, `plan_merge`, `result_cache`, `full_results`, `server.py` paths that moved); fix each one
- [X] T115 Run `/speckit-converge` against this tasks file and the spec; append any remaining work as new tasks
- [X] T116 Ask the maintainer whether to raise the version, as `docs/versioning.md` requires; do not change `pyproject.toml` without their answer. Done: the maintainer approved a MINOR bump to 0.9.0 (commit 4402286).
- [X] T117 Review the branch history with `git log --oneline master..refactor/source-structure`; there must be one commit per phase (FR-021) and no commit on master

---

## Dependencies & Execution Order

### Phase dependencies

- **Setup (Phase 1)**: No dependencies.
- **Foundational (Phase 2)**: Depends on Setup. Blocks every story.
- **US1 (Phase 3)**: Depends on Foundational. MVP.
- **US2 (Phase 4)**: Depends on US1 (green gate). Blocks US3, US4, and US5, because they rely on the leaf modules and the empty violation list.
- **US3 (Phase 5)**: Depends on US2.
- **US4 (Phase 6)**: Depends on US2. Independent of US3 in code, but both edit `service.py` and tests, so run them in order.
- **US5 (Phase 7)**: Depends on US3 and US4, because it edits the files those phases move (`jobs/runner.py`, `desktop/app.py`, `server.py`).
- **US6 (Phase 8)**: Depends on US3 and US4, because it renames and splits test files that those phases move.
- **Polish (Final)**: Depends on all stories.

### Within each story

- Leaf modules and their tests before rewiring callers.
- Moves before the import updates that depend on them.
- Gate (QC, surface contract, commit) last.

### Parallel opportunities

- Phase 2: T006 and T007 can run alongside T003–T005.
- Phase 4: the leaf modules T017–T026 are all independent (different new files). T037 and T038 touch different files.
- Phase 5: T065 and T066 are docs in different files.
- Phase 6: T072, T073, T076, T078, T079, and T080 create new files and can run in parallel.
- Phase 8: T103 and T106 touch different files.
- Final: T110–T112 can run in parallel.

### Parallel execution example (Phase 4 leaf modules)

```text
Task: T017 Create src/mcp_hub/jsonio.py
Task: T019 Create src/mcp_hub/textnorm.py
Task: T021 Create src/mcp_hub/clients.py
Task: T023 Create src/mcp_hub/tool_meta.py
Task: T025 Create src/mcp_hub/progress.py
```

---

## Implementation Strategy

### MVP first (User Story 1 only)

1. Phase 1 and Phase 2 (baseline and guard).
2. Phase 3 (US1): QC green on the branch.
3. **Stop and validate**: `package_qc.py` exits 0.

### Incremental delivery

1. Add US2 → layering test passes, helpers exist once → commit.
2. Add US3 → `server.py` under 600 lines, surface unchanged → commit.
3. Add US4 → jobs and desktop split, settle path tested → commit.
4. Add US5 → tunables moved, defaults tested → commit.
5. Add US6 → tests organized → commit.
6. Polish and version question.

---

## Completion summary (for the reviewer of this file)

- **Total tasks**: 117 (Setup 2, Foundational 8, US1 6, US2 32, US3 21, US4 20, US5 12, US6 8, Polish 8)
- **Tasks per story**: US1 6 · US2 32 · US3 21 · US4 20 · US5 12 · US6 8
- **MVP scope**: Phases 1–3 (T001–T016), which return the quality gate to green and create the baseline guard
- **Independent test per story**: listed at the top of each phase
- **Format check**: every task is `- [ ] Txxx [P?] [USn?] description with path`; Setup, Foundational, and Polish carry no story label

## Phase 9: Convergence

**Purpose**: Remaining gaps between the spec, plan, and tasks and the code at HEAD, found by `/speckit-converge`. Each task is independent and can be done alone. The constitution is an unfilled template, so no constitution findings apply.

- [X] T120 Consolidate test fakes that share a name across test modules (FakeClient in six modules, FakeChild, FakeClock, FakeCondenser, FakeOpener, FakeProtector, FakeStore) into tests/helpers, and rename the two different FakeResponse classes in tests/helpers/desktop.py and tests/helpers/fakes.py so each name means one thing; fakes whose behaviour differs stay separate with distinct names per FR-018 (partial)
- [X] T121 Split src/mcp_hub/jobs/store.py (611 lines) so no module under src/mcp_hub exceeds 600 lines, keeping its public methods, return values, and audit events unchanged per SC-002 (partial)
- [X] T122 Run the two failing checks of scripts/smoke.py (mcp-server-logs list_openobserve_organizations: fetch failed; mcp-sqlserver check_connection: TimeoutError) against the pre-refactor commit 08bad0f on the same machine. If they pass there, find and fix the regression; if they fail there too, record the environmental cause in this file's notes per SC-009 via T087 (partial)
  - T122 result: the pre-refactor commit 08bad0f, run with the same config and sibling children, fails the same two checks: mcp-server-logs (list_openobserve_organizations: fetch failed) and mcp-sqlserver (check_connection: TimeoutError). Both are environmental (upstream reachability), not a regression. mcp-read-source failed only in the worktree (it has no .venv), an artifact of the comparison.
- [X] T123 Add tests/test_job_approvals.py that imports mcp_hub.jobs.approvals and covers approve_job with a correct code, a wrong code, an expired code, and rejection if the job API defines one (state in the test that it does not otherwise); T072 named this file and it does not exist yet per US2 and FR-019 (partial)
- [X] T124 Update plan.md (Project Structure and target layout) and data-model.md to the implemented layout: desktop/cli.py instead of desktop/__main__.py (see T082), call_observers.py, results/runtime.py, bootstrap.py, and the desktop mixin modules (appearance, actions, live_feeds, polling, render, update_controller, stream) per plan: target layout (contradicts)
- [X] T125 Decide T106 (rename the moved test modules, for example tests/test_plan_merge.py to tests/test_planning_merge.py, and update the docs that cite them) or record it as cancelled in this file's notes per plan: test layout (partial)
  - T125 decision: T106 (renaming test modules) is recorded as cancelled. The docs cite test file names, a rename changes no behaviour, and the names already describe the tests; reopen T106 only if a rename is wanted later.

# Feature Specification: Source Structure Refactor

**Feature Branch**: `refactor/source-structure`
**Created**: 2026-11-16
**Status**: Draft
**Input**: User description: "Source-structure refactor of mcp-hub, behavior-preserving. Fix the broken AGENTS.md link to agent-plans/README.md so the docs check and package QC pass; split oversized modules; consolidate duplicated helpers; move hard-coded tunables into settings; fix layering violations; clean up test fakes."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Restore a green quality gate (Priority: P1)

A maintainer runs the repository's offline quality gate on the master branch and gets a passing result. Today the docs check fails because AGENTS.md links to a plan folder that was deleted, which turns the whole gate red.

**Why this priority**: Every later change is verified by this gate. A red baseline hides real regressions, so it must be fixed first.

**Independent Test**: Run the offline quality gate on the refactor branch. It passes with no failing tests and no docs-check problems.

**Acceptance Scenarios**:

1. **Given** AGENTS.md points to a deleted file, **When** the docs check runs, **Then** no broken link is reported and the check passes.
2. **Given** the full unit test suite, **When** it runs, **Then** all tests pass and the coverage gate (85 percent or more) is met.
3. **Given** the planning rules in AGENTS.md, **When** a contributor looks for where plans live, **Then** the instructions point to the Spec Kit `specs/` folder and its `/speckit-*` commands.

---

### User Story 2 - Clean dependency direction and shared helpers (Priority: P1)

A maintainer changes a low-level module (for example the JSON settings store or the bridge client) without pulling in unrelated runtime code. Shared helpers exist once, not copied across modules.

**Why this priority**: Wrong dependency direction makes every later split harder. It is also the cheapest safe change to make first.

**Independent Test**: Import the bridge module in a fresh process and confirm it does not load the server runtime. Confirm each duplicated helper now has a single definition that all callers use.

**Acceptance Scenarios**:

1. **Given** the bridge client, **When** it is loaded, **Then** it does not import the server runtime module.
2. **Given** jobs and agents code, **When** they need the read-only tool check, **Then** they get it from a shared module, not from the server module.
3. **Given** the planning and results layers, **When** the dependency graph is inspected, **Then** the results layer does not depend on the planning layer.
4. **Given** code that reads another module's underscore-prefixed helper, **When** it is reviewed, **Then** the helper is public and imported by its name without the underscore.
5. **Given** JSON files are written by the settings, tool pin, update state, service config, and BitNet installer modules, **When** a write is interrupted, **Then** each file is replaced atomically through one shared helper.

---

### User Story 3 - Split the central server module (Priority: P2)

A maintainer finds the code for planning, result condensing, batch calls, and instruction loading in clearly named places, not inside a single module of about 1,350 lines.

**Why this priority**: The server module is the largest hotspot and the riskiest to change. Splitting it reduces review cost and regression risk for later work.

**Independent Test**: Run the existing tests and the smoke check. Compare the tool list, tool schemas, and call results before and after the split. They must be identical.

**Acceptance Scenarios**:

1. **Given** the refactored server module, **When** its length is measured, **Then** it is below 600 lines and holds only the runtime, routing, dispatch, and entry-point code.
2. **Given** the tools exposed to MCP clients, **When** they are listed before and after the refactor, **Then** names, descriptions, and input schemas are unchanged.
3. **Given** a progress notification from a child server, **When** it is forwarded through the hub or the bridge, **Then** both paths use one shared implementation.
4. **Given** the service startup and the stdio server entry point, **When** they load configuration and settings, **Then** they use one shared bootstrap helper.

---

### User Story 4 - Split the job runner and the desktop app (Priority: P2)

A maintainer working on investigation jobs or the desktop status window finds tool schemas, approvals, investigation steps, appearance, rendering, update flow, streaming, and mini mode each in their own module.

**Why this priority**: The job runner and desktop window are the next largest hotspots. Splitting them follows the same pattern as User Story 3.

**Independent Test**: Run the job and desktop test suites. Launch the desktop status window and confirm the status, update card, mini mode, and usage and processing streams still work.

**Acceptance Scenarios**:

1. **Given** the job runner, **When** a job is interrupted during settlement, **Then** the synchronous database work does not block the event loop (it runs in a worker thread), and a test covers this.
2. **Given** the desktop app module, **When** its size is measured, **Then** each new module holds one concern and no function exceeds 50 lines.
3. **Given** the usage and processing streams, **When** they reconnect after a dropped connection, **Then** both use one shared stream controller with the same retry behavior as before.
4. **Given** the desktop package, **When** its imports are checked, **Then** it imports nothing from the Hub package outside the desktop package.

---

### User Story 5 - Move tunables into settings (Priority: P3)

An operator who wants to change a timeout, a size limit, or a batch interval finds it as a documented key in the settings module, not as a number buried in code.

**Why this priority**: Important for operators, but it does not block the structural work above.

**Independent Test**: Change one of the moved settings in the configuration and confirm the affected component uses the new value. Confirm the documentation lists the key and its default.

**Acceptance Scenarios**:

1. **Given** a BitNet, agent, job, or activity setting that was hard-coded, **When** the settings are listed, **Then** the value appears as a documented key with the same default as before.
2. **Given** the default of 9000 characters repeated in several modules, **When** the code is searched, **Then** it is defined in exactly one place.
3. **Given** the desktop app cannot read the Hub settings module, **When** a desktop constant mirrors a Hub setting, **Then** a comment names that setting so the two stay in sync.

---

### User Story 6 - Share test fakes (Priority: P3)

A maintainer writing a new test imports fake clients, responses, runtimes, and event helpers from one helper package, not from another test file.

**Why this priority**: Improves test maintenance. It does not change product behavior.

**Independent Test**: Search the tests folder for cross-test imports and for copied fake classes. Neither should remain.

**Acceptance Scenarios**:

1. **Given** the test suite, **When** test modules are searched for imports from other test modules, **Then** none are found.
2. **Given** fake classes used by more than one test, **When** the tests folder is searched, **Then** each fake is defined once in the shared helper package.
3. **Given** the large desktop window test file, **When** it is split, **Then** each resulting file covers one desktop module and all tests still pass.

---

### Edge Cases

- What happens when a moved module is still imported by an old path (for example a script or a documented command)? Every reference in code, scripts, and docs must be updated in the same change, and the docs check must pass.
- How does the system behave if a settings key is removed or renamed? It must not be removed or renamed; only new keys are added, with the previous default.
- What if a split leaves a module import cycle? The dependency-direction check must fail the change before merge.
- What if a function grows over 50 lines during a move? The code standards check must fail the change.
- What happens when the progress-forwarding code handles a child that sends no progress? Behavior must match the current code: no notification is sent.
- How does the system handle an interrupted JSON write? The atomic helper must leave either the old file or the new file, never a partial file.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The repository's offline quality gate MUST pass on the refactor branch before and after every phase.
- **FR-002**: The docs check MUST report no broken links, including the link in AGENTS.md that points to the removed plan folder.
- **FR-003**: AGENTS.md and the affected docs MUST describe the Spec Kit planning flow (`specs/` and `/speckit-*` commands) in place of the removed plan folder.
- **FR-004**: The bridge client MUST NOT import the server runtime module.
- **FR-005**: The planning layer MUST NOT be imported by the results layer, and core modules MUST NOT import subpackages that sit above them.
- **FR-006**: No module MUST import an underscore-prefixed name from another module.
- **FR-007**: Each duplicated JSON read and write helper MUST be replaced by one shared implementation, and each write MUST be atomic.
- **FR-008**: Progress and metadata forwarding MUST have one shared implementation used by both the server and the bridge.
- **FR-009**: The server runtime module MUST be split so that planning, result condensing, batch handling, pin screening, and instruction loading each live in their own module, and the server module keeps only runtime, routing, dispatch, and entry-point code.
- **FR-010**: The MCP tool names, descriptions, input schemas, routes, audit event names, settings keys, and command-line entry points MUST remain unchanged.
- **FR-011**: The job runner MUST be split so that tool schemas, approvals, and investigation logic each live in their own module.
- **FR-012**: Synchronous database calls inside asynchronous job code MUST run in a worker thread.
- **FR-013**: The desktop app module MUST be split so that appearance, rendering, update flow, streaming, mini mode, and command-line code each live in their own module.
- **FR-014**: The usage and processing streams MUST share one stream controller with the same reconnect behavior.
- **FR-015**: The desktop package MUST continue to import nothing from the Hub package outside itself.
- **FR-016**: Each hard-coded tunable listed in the review (BitNet, agents, jobs, activity UI) MUST become a documented settings key with the same default value.
- **FR-017**: The value 9000 used for condensing MUST be defined in exactly one place.
- **FR-018**: Test fakes used by more than one test MUST live in one shared helper package, and tests MUST NOT import from other test modules.
- **FR-019**: Every new or moved module MUST be imported by at least one test, and every new helper MUST have tests for its success and failure paths.
- **FR-020**: Every change that moves or adds a module, a setting, or a command MUST update the matching documentation in the same change.
- **FR-021**: Each phase MUST be one commit on the refactor branch. No commit MUST be pushed to master in this feature.

### Key Entities *(include if feature involves data)*

- **Refactor Phase**: One step of the plan. It has a goal, a list of files it may change, and the checks that must pass before it is complete.
- **Module Boundary**: A rule about which module may import which. It is enforced by the docs and code checks.
- **Settings Key**: A documented setting with a name, a default, and a meaning. Existing keys keep their names and defaults.
- **Shared Helper**: A single implementation of a common operation (JSON read and write, progress forwarding, stream control, test fake) used by several callers.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: The offline quality gate passes with zero failing tests after Phase 1 and after every later phase.
- **SC-002**: The refactored server module is under 600 lines, and no module in the package is over 600 lines.
- **SC-003**: No function in the source code is over 50 lines.
- **SC-004**: The bridge client does not load the server runtime module, verified by an import test.
- **SC-005**: Zero cross-test-module imports and zero copied fake classes remain in the tests folder.
- **SC-006**: The tool list returned to MCP clients is identical before and after the refactor, including names and input schemas.
- **SC-007**: Latency of the tool-call path does not increase by more than 5 percent compared with the baseline measured before the refactor.
- **SC-008**: Every moved tunable appears in the settings documentation with its original default.
- **SC-009**: The desktop status window shows status, the update card, mini mode, and both streams working after the desktop phase.

## Assumptions

- The refactor preserves behavior exactly. Any behavior change found during the work is reported separately and is out of scope.
- The refactor happens on the `refactor/source-structure` branch, which is created from master. Merging to master is a separate decision by the maintainer.
- Phases are executed in order: the quality-gate and dependency fixes first, then the server split, then the job and desktop splits, then the settings moves, then the test cleanup.
- Parallel work by several agents is allowed, but each agent works in its own git worktree and only changes files in its assigned area.
- The Spec Kit templates installed by `specify init` in this repository are the planning templates; the repository's own rules in AGENTS.md continue to apply to all code and docs.
- Version bumps are out of scope; they require the maintainer's separate approval.
- Child MCP servers and their upstream services are not changed by this feature.

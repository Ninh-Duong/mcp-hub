# Tasks: Playbook Use Case Workflows

## Setup
- [X] T001 Record approved spec, implementation decisions and interface contracts.

## US1 - Five workflows
- [X] T002 Add JSON workflow parsing, validation and explicit subflow resolution.
- [X] T003 Generalize execution state, scope guards and receipt interpretation for all five playbooks.
- [X] T004 Define all playbook branches, tool inputs, evidence and stop conditions in their owning files.

## US2 - Context and refresh
- [X] T005 Implement provide_input, fixed scope and missing-context states.
- [X] T006 Implement validated continue_from with write-only expansion and predecessor retirement.
- [X] T007 Handle PR revisions, truncation, source-path evidence and refresh permissions.

## US3 - Usage and validation
- [X] T008 Rewrite README and synchronize policy, client rules and contracts.
- [X] T009 Add offline scenarios for all branches, concurrency, continuation and five README examples.
- [ ] T010 Review changes, update surface baselines and pass package QC and live smoke.

## Dependencies
T001 -> T002 -> T003/T004 -> T005/T006/T007 -> T008/T009 -> T010. Each story has independent acceptance cases in spec.md. Implementation follows this order without parallel agents.

## Validation results

Final package QC passed: 1,263 tests, three environment-dependent skips, 92% overall line/branch coverage. Workflow definition/state/scope/runtime coverage is 88%/95%/94%/97%. Lint, docs, privacy scanning, code standards and compilation passed. Surface baselines intentionally include the new plan inputs, provide_input and bounded workflow input setting.

T010 remains open only because live smoke did not pass. The Hub connected all five children; Jira, PR and source passed. Log upstream checks returned fetch failures and the SQL connection check timed out. No account changes, automatic retries, commit or release were performed. The earlier merge's smoke waiver was not reused. Version remains 0.9.0 pending the user's choice.

Review also verified that known service names skip discovery, access failures block progress, Error rows precede related Warning rows, asset reads require ticket-listed names, source reads require searched/user-bound paths, and refresh continuations do not execute calls during cloning.

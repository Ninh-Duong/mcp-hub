# Tasks: Client Workflow Harness

## Setup

- [X] T001 Record approved specification, architecture decisions and MCP contract.

## US1: Correct process

- [X] T002 Add validated workflow stage definitions to playbooks and pilot plan scope.
- [X] T003 Implement bounded workflow state, receipts, scope validation and transition rules.
- [X] T004 Integrate direct calls and atomic batch validation with execution locking.

## US2: Progress

- [X] T005 Publish hub__workflow status/report/finish with validated references and metadata-only audit.
- [X] T006 Add process counters/reporting and client guidance.

## US3: Evidence

- [X] T007 Preserve receipt/full-result metadata through condensation and batch packing; report expiry safely.

## Validation

- [X] T008 Add offline state-machine, runtime, failure/concurrency and compatibility tests.
- [X] T009 Update owning docs and client/playbook instructions, review diff and run package QC and smoke.

## Validation results

Package QC passed: 1,246 tests, 3 environment-dependent skips, 92% overall coverage; workflow modules 97% combined coverage. Lint, leak, code standards, docs and compile checks passed.

Live smoke was executed but did not pass: the Hub connected all five children; Jira, PR and source checks passed, while log upstream checks failed and SQL's check timed out. This remains an external-integration validation limitation; no account/configuration changes or automatic retries were made. No commit or release is performed while smoke is failing. The version remains unchanged pending the user's release choice.

## Dependencies

T002 -> T003 -> T004 -> T005 -> T006/T007 -> T008 -> T009. US1 is the pilot MVP; US2 and US3 extend its observable behavior. Independent state and transport test scenarios can be authored separately after T005.

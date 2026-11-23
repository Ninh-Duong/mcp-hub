# Feature Specification: Playbook Use Case Workflows

**Feature Branch**: `003-playbook-usecase-workflows`
**Created**: 2026-11-23
**Status**: Approved for implementation
**Input**: Implement the reviewed plan for all five client-controlled playbooks and a simple usage-first README.

## User Scenarios & Testing

### US1 - Follow a complete playbook (P1)

Users select rcid-bug, jira-ticket-bug, service-errors, pr-review or source-search for a new task. Each stage names its tools, required context, permission, evidence and stop conditions. Jira investigates bugs; PR reviews changes. Acceptance: out-of-order calls, cross-stage batches and changed scope never reach a child. Cache-first branches, empty/truncated data and source follow-up are explicitly represented.

### US2 - Supply missing context and grant cache refresh (P1)

Clients ask for missing log times, ambiguous service/PR selection and refresh permission when required. Acceptance: no default one-hour search; locked context cannot change; inherited plans add only approved refresh writes without replaying successful calls. Unknown cache freshness is not represented as fresh. Access errors block progress.

### US3 - Use and inspect the product (P1)

Users copy one of five README examples and see playbook selection, requests for missing context, permission requests and evidence-backed output. Acceptance: README explains setup, everyday use, errors and links in simple English; technical state details remain in owning docs.

## Functional Requirements

- FR-001: Keep one Markdown definition per playbook and one catalog; embed validated JSON workflow definitions, with no executable expressions.
- FR-002: Reuse bounded plan state, receipts, retry, batch, cancellation, evidence paging and audit. Execution facts and client-reported interpretations remain distinct.
- FR-003: Add provide_input and continue_from. Inputs are filled once; continuations preserve scope/progress and retire their predecessor.
- FR-004: PR write annotations are respected before any freshness/context/diff/comment call; no implicit bulk sync.
- FR-005: Bind tool inputs to the plan's project/env/window and task identifiers. Source reads use searched or user-provided paths. Do not claim exact repository counts for bounded searches.
- FR-006: Keep investigator jobs unchanged and add no SQL playbook, dependencies, private configuration changes or model loop.
- FR-007: Update policy, client rules, contracts, settings/audit docs, tests and README together. Both QC and smoke are required; previous smoke waiver is not inherited.

## Success Criteria

All five workflows pass offline happy/failure/branch cases; no invalid call reaches a child. Missing time is requested before log reading. Continuation causes no replay or scope expansion. README has five usable examples and every owning-document link resolves. Full package QC meets the existing coverage gate.

## Assumptions

The client reports semantic findings and user choices truthfully, as with current plan/write declarations. The Hub cannot authenticate a chat approval independently. Evidence stays in the existing bounded stores. No arbitrary user content enters audit events.

# Feature Specification: Ticket Requirements Review

**Feature Branch**: `004-ticket-review`
**Created**: 2026-11-23
**Status**: Approved for implementation
**Input**: Review a Jira ticket, identify missing requirements and confirmations, confirm selected BE/database/optional FE sources before comparison, assess API/SP changes and related feature impact, and separate questions for BE, FE and QC.

## User Scenarios & Testing

### US1 - Review ticket clarity (P1)

Read the ticket and history first, plus listed attachments when needed. Extract intent, acceptance criteria, business rules and contradictions; draft questions instead of inventing answers. A missing/stale cache may only refresh after user permission. Acceptance: no source tool runs before ticket evidence and explicit source confirmation.

### US2 - Confirm and compare multiple sources (P1)

Ask which registered BE, DB and optional FE source aliases to compare. The user may identify directories in chat, but reading remains through registered source tools; no arbitrary disk fallback or implicit registration. Acceptance: only selected aliases are readable, and source-read paths are backed by search evidence from the same alias. Adding/replacing sources after confirmation requires a new scoped task.

### US3 - Deliver actionable confirmations and impact (P1)

Report what the ticket requires, what current source supports, candidate API/SP/schema changes and unresolved questions. Separate common, BE, FE and QC confirmations; identify related features, evidence and regression scenarios. FE/QC conclusions without matching source evidence remain hypotheses. Unreadable or bounded source cannot be represented as absence of implementation.

## Requirements

- FR-001: Add ticket-review as a separate playbook, retaining jira-ticket-bug for production bug investigation.
- FR-002: Read with Jira tools and compare with source search/read only after a user interaction stage.
- FR-003: Support multiple explicitly selected registered source roles without expanding other playbooks' project scope.
- FR-004: Preserve cache permissions, continuation scope, evidence, access handling and output shapes.
- FR-005: Provide a consistent review report with confirmation owner, priority, evidence, acceptance criteria and impact confidence.
- FR-006: Do not edit code, post Jira comments, execute SQL or claim live database parity as part of the default review. Read extra tickets only after explicit selection/confirmation and isolate their evidence/attachments by issue key.
- FR-007: Keep runtime evidence private; docs/tests use synthetic keys and registered-alias placeholders.

## Success Criteria

Offline tests demonstrate ticket-first and user-confirmed-source ordering, two-source BE/DB comparison, optional FE, source isolation, permission/attachment failures and the report contract. Catalog and README expose the new use case. Existing five workflows remain compatible.

## Assumptions

User confirmed that database source files are primary; live DB access requires a separate request. User also selected optional related-ticket reads after explicit selection/confirmation in this review. Missing related evidence is a limitation, not permission to discover more tickets. User choices and semantic findings are client-reported, never independently certified by the Hub.

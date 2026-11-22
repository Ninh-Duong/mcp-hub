# Feature Specification: Client Workflow Harness

**Created**: 2026-11-22
**Feature Branch**: `002-client-workflow-harness`
**Status**: Approved for implementation
**Input**: Implement the reviewed client-controlled harness, prioritizing correct process with rcid-bug as the pilot.

## User Scenarios & Testing

### User Story 1 - Follow the required process (Priority: P1)

An AI client checks cached evidence before upstream queries and follows the request's fixed scope.
Acceptance: upstream-before-cache and source-before-findings calls are refused before any child runs. Environment, request ID, targets and absolute time window cannot change inside the plan. Failed calls do not complete a step. Batches cannot cross dependent stages.

### User Story 2 - Inspect progress and limitations (Priority: P2)

A client sees the current stage, allowed tools and recorded call outcomes, reports findings, and ends with supported, insufficient or blocked status.
Acceptance: references must belong to executed calls in this plan. Semantic findings are explicitly client-reported. State-changing operations fail while calls are active. Restart or plan eviction requires a new plan.

### User Story 3 - Retrieve evidence (Priority: P3)

A client can retrieve an existing full-result reference without repeating an upstream query.
Acceptance: workflow metadata survives condensation and batch packing without changing child output schemas or structured content. Expired evidence reports expiry without automatically replaying tools.

## Requirements

- FR-001: Apply workflow enforcement to rcid-bug direct calls only; preserve existing policy and confirmation behavior.
- FR-002: Bind a plan to project, environment, rcid, log targets and absolute window; default to the preceding hour.
- FR-003: Define machine-readable stages with cache listing, cache reading, upstream logs, findings and optional source reading.
- FR-004: Validate scope and stage before reserving a call; record success/failure/cancellation and release reservations reliably.
- FR-005: Offer progress, findings and finish operations without adding a Hub model loop.
- FR-006: Distinguish execution facts from client-reported semantic claims. Access/configuration failures require a blocked finish.
- FR-007: Preserve normal retry, cache, allowlist, redaction, audit, schema and timeout handling.
- FR-008: Audit bounded metadata only; expose process counters and timings without storing raw evidence in workflow state.
- FR-009: Keep workflow lifetime tied to bounded in-memory plans. Add no dependency or job-store migration.

## Success Criteria

- Every tested out-of-order or out-of-scope call is refused before child execution.
- A cache-supported scenario completes without an upstream call.
- Failed, cancelled, expired and concurrent operations leave a consistent, inspectable state.
- Existing non-pilot scenarios continue passing their regression tests.

## Assumptions

The client controls reasoning. Findings about cache coverage, error rows and explanation quality are assertions, never verified conclusions. Existing project agent rules govern implementation; the constitution file currently contains an unfilled template. No automatic replay or durable workflow resume is included.

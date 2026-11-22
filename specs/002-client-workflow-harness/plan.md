# Implementation Plan: Client Workflow Harness

## Summary

Implement the approved rcid-bug pilot on the existing direct-call path. The client remains the reasoning agent. Workflow state sits in planning; runtime hooks reserve calls, attach receipts, expose the MCP interface and audit transitions.

## Technical Context and Constitution Check

Python, asyncio, existing MCP SDK and unittest. No dependencies or migrations. State lives on Plan and is evicted with PlanBook. Use a per-workflow asyncio lock for direct/batch execution; reject report/finish while locked. The constitution is an unfilled template; AGENTS.md applies. Keep functions below 50 lines, imports one-way and tests offline. No account files are read.

## Implementation

1. Machine-readable stage/tool lists in rcid-bug frontmatter, validated at startup.
2. State and receipts on Plan; require rcid/project/env, fix an absolute window, enforce scope/stage before execution.
3. Runtime workflow mixin integrates direct calls, prevalidated batches, result metadata and hub__workflow status/report/finish.
4. Documentation and metadata-only audit/reporting; preserve non-pilot behavior and policy modes.
5. Offline regression tests, full package QC and smoke. No version bump without user choice.

## Validation

Cover cache-only completion, cache miss, scope/stage violations, retry/cancellation, reference validation, concurrent transitions, result shapes, batch dependency checks, restart/eviction and expiry.

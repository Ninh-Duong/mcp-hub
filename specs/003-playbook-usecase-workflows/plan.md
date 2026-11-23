# Implementation Plan: Playbook Use Case Workflows

## Summary and technical context

Extend the current Python/asyncio harness and MCP tools without a new dependency or Hub model loop. Each playbook owns a validated JSON graph; explicit one-level subflows let Jira reuse logs workflows. Existing includes merges prose/tools only.

## Constitution check

The constitution remains an unfilled template. AGENTS.md governs one-way dependencies, English text, bounded single-purpose functions, temporary offline tests, secret-free audit and same-change docs. No child implementation or private account files are touched.

## Implementation sequence

1. Add workflow definition parser/compiler below runtime. Validate graph references, tool membership and cycles at startup.
2. Add shared scope and result-observation helpers; replace pilot transitions with graph nodes while preserving receipt/batch/cancellation hooks.
3. Add fill-once context and missing-input status; defer log targets until logs begin. Implement same-scope continuation with copied call history and predecessor retirement after validation.
4. Define all five workflows and explicit branches/tool contracts. Bind source paths and PR revisions to evidence when provided; preserve unknown completeness instead of inventing certainty.
5. Rewrite README around setup, five examples, prompts and errors. Synchronize owning rule/docs and add offline tests, baseline changes and full QC/smoke.

## Interfaces and verification

See contracts/README.md for hub__plan and hub__workflow additions. Test every branch, denied scope/step/write, waiting input, continuation, malformed definitions, cancellation/concurrency, PR revision changes, bounded source counts, shape preservation and expiry. Version remains unchanged until the user explicitly chooses a bump.

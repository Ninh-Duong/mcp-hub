# Implementation Plan: Ticket Review

## Summary and context

Add a sixth client-controlled playbook for requirement clarity and code impact. Use Jira read/sync/asset and registered source search/read tools. Primary DB evidence is the selected database source repository; live SQL remains a separate explicit task. Read selected related tickets only after review-scope confirmation.

## Constitution and boundaries

AGENTS.md governs English tracked text, small functions, offline tests, one-way imports and private data boundaries. Reuse the existing harness, settings and stores; no dependencies, external writes, source execution, raw-root reading or shared-service restart.

## Sequence

1. Extend data-only graph definitions with explicit multi_source and interaction-only nodes, retaining existing graph behavior.
2. Add bounded role aliases and related ticket selection validation; require confirmation before additional reads. Isolate source-path receipts by alias and Jira attachments/refresh prerequisites by issue.
3. Add the ticket-review graph, explicit read tools and review/confirmation/impact output contract.
4. Update catalog, README and policy/client rules; test selection, two-source paths, cache/attachment errors and compatibility.
5. Run focused tests, full package QC and smoke. Keep version unchanged until the user's release choice.

## Acceptance

Ticket is read first; no source call executes before selected aliases are confirmed. Only selected aliases are readable. Searching a path in BE does not unlock that path in DB. Final output distinguishes common/BE/FE/QC questions and evidenced vs possible feature impact; no Jira comment or code/DB change occurs.

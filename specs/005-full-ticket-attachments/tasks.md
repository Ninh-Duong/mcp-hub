# Tasks: Complete Ticket Attachments

- [X] T001 Specify default complete sync, limits, context and compatibility.
- [X] T002 [US1] Add failing mixed-attachment and completeness regression tests in the child.
- [X] T003 [US1] Implement encrypted all-type sync, safe metadata, reuse, repair and explicit partial results.
- [X] T004 [US3] Extend existing asset read to cached document resources; preserve image reads and access checks.
- [X] T005 [US2] Update child/Hub contracts, usage and attachment-to-case guidance.
- [X] T006 Review and run child checks, Python/MCP compatibility, Hub QC and smoke; record results.

## Validation results

- Child: 50 offline tests passed with the Hub interpreter (Python 3.12, MCP 2.2), Python 3.10/current MCP, and Python 3.10/MCP 1.x. Environment, compilation, public-repository hygiene and focused lint passed.
- Hub package QC: 1,277 tests, three environment-dependent skips, 92% coverage; lint, privacy, standards, documentation and compilation passed.
- Smoke: Jira, PR, source and Hub passed. Logs failed in dev/stg/prod with fetch failed (requests 7/8/9); SQL failed with TimeoutError (request 13). Global smoke is not green; these failures are outside the attachment implementation and no account configuration was changed.
- Review covered exact-byte retrieval through mounted MCP tools, encryption, legacy image compatibility, all-type backfill, missing-file repair, failed replacements, duplicate names, original-name context and bounded downloads. No blocking code findings remain.
- No real ticket content was added to files or fixtures. After the external smoke failures were disclosed, the user requested committing, pushing and merging both repositories into master. No live ticket resync, service restart or version increase is part of that request. Hub changes are documentation and Spec Kit artifacts only; its version is unchanged.
- Publication: child implementation merged in [mcp-jira-sync PR 1](https://github.com/Ninh-Duong/mcp-jira-sync/pull/1), merge 3f8fdd0, after all 12 push/PR CI checks passed (three operating systems, Python 3.10/3.12). Repeated pre-commit child tests and Hub package QC passed with the same totals.

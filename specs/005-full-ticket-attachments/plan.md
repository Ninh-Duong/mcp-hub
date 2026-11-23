# Implementation Plan: Complete Ticket Attachments

## Summary

Extend the Jira child so ordinary sync caches every attachment type. Preserve tool names/arguments, encryption, per-file limits, image output and unchanged-file reuse. The Hub changes only its contract, playbook guidance and Spec Kit artifacts.

## Design and decisions

- Add a focused child core attachment helper for safe unique cached names, metadata, download/reuse and cache completeness.
- Catalog records original/cached names, MIME, size, Jira identity, status and an attachment schema marker. Legacy entries, incomplete downloads and missing cached assets trigger ordinary-sync repair.
- Keep assets_count and existing SyncResult image counters compatible; add total/cached/unavailable counters. Ticket Markdown lists every file with status and original filename; only cached files have local read links.
- Keep jira_read_asset arguments unchanged. Use manifest metadata to authorize reads: images retain image blocks, other bytes return MCP embedded resources with a Jira attachment URI and MIME. Legacy image caches remain readable. No document parsers or new dependencies.
- Preserve id/size reuse and purge removed/failed old copies. Prevent path traversal, reserved filenames and case-insensitive/sanitized collisions. Never write plaintext ticket bytes or fixtures containing real project data.

## Validation and rollout

Write regression tests first for mixed types, encrypted bytes, mounted reads, old/missing cache repair, partial failures, limits, collisions and deletion. Run the child suite/environment/compile checks with Python 3.10 and MCP 1.x compatibility. Run Hub package QC and smoke after docs changes. Report external smoke failures separately. Changes stay on feature branches without commit, push, merge, live resync, service restart or version increase unless requested.

## Governance

Both repositories' AGENTS.md and child compatibility contract apply. The local constitution is an unfilled template. No extension hooks are configured. All requirements checklist items pass.

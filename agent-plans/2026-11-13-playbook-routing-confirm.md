# Playbook routing with user confirmation, and source-search playbook

Status: approved

## Context

A prompt such as "find the global functions that split on 'a,d,h' in wec.be" took 20 to 30 minutes in Antigravity. The Hub spent about 3 seconds (the BitNet planner timeout). Almost all the time went to the agent scanning the repository by itself after `source__search` failed with `Unknown project alias`, and to model thinking between calls. The Hub has playbooks (`docs/playbooks/*.md`, parsed by `src/mcp_hub/playbooks.py`) whose frontmatter the Hub enforces, but it has no playbook for code search, and it does not ask the user which playbook to use.

Intended outcome: the Hub recognizes the kind of request, proposes the matching playbook, and waits for the user to confirm it before any child call. A code-search playbook covers `mcp-read-source`. An unknown project is registered only after the user confirms it.

## User stories

- **US1 (P1)** As a user, I confirm which playbook the Hub uses before it calls any child, so a vague prompt cannot start a long scan.
  - Acceptance: `hub__plan` without `playbook` returns candidates and a `confirm_token` and creates no plan; with the right token and a chosen playbook, it creates the plan.
- **US2 (P1)** As a user, a code search in a registered repository finishes in seconds through the `source-search` playbook.
  - Acceptance: the plan of the playbook names only `mcp-read-source`, and its steps stop on a child error.
- **US3 (P2)** As a user, an unregistered repository is added only after I confirm its path and alias.
  - Acceptance: `needs_registration` returns a proposal with a token; registration needs that token and never accepts a path outside the rules below.
- **US4 (P2)** As a user, I can choose how strict confirmation is (`always`, `ambiguous`, `off`).

## Functional requirements

- **FR-001** `policy.confirmPlaybook` setting in `settings.py`: `always` (default), `ambiguous`, or `off`. Documented in `docs/settings.md`.
- **FR-002** When `hub__plan` has no `playbook` and the setting asks for confirmation, the Hub returns the candidate playbooks, each with its `when` text and a short reason from `bitnet/rules.py`, plus a `confirm_token` bound to the task text and the candidate list. No plan is stored.
- **FR-003** `hub__plan` with `playbook` and a valid, unexpired `confirm_token` creates the plan as today. A missing, wrong, reused, or expired token is refused with a `Policy:` message that says what to do.
- **FR-004** Under `ambiguous`, the Hub asks only when `rules.py` cannot pick one playbook. Under `off`, behavior is unchanged.
- **FR-005** BitNet assist runs only when the request still has no playbook after rules. When a playbook is given, the BitNet planner is skipped.
- **FR-006** New playbook `docs/playbooks/source-search.md`: `servers: [mcp-read-source]`, `tools: [source__search, source__read]`, `needs: [source]`, and a stop rule: on a child error, stop and report it; never search the disk by another means.
- **FR-007** `needs: [source]` in `playbooks.py` requires the project alias to be registered with `mcp-read-source`. Without it, the plan returns `needs_registration` (FR-008) instead of a plain error.
- **FR-008** Registration: the Hub returns a proposal (alias, path given by the agent, confirm token). The agent asks the user. On confirmation, the agent calls `source__register_project` (alias, path, token). The tool is declared in the plan's `writes`, is not read-only, and exists only in `mcp-read-source`.
- **FR-009** `mcp-read-source` validates the path: it must be an existing directory, not a drive root or a system folder, without `..` segments. It then writes the alias to its `config/private/projects.json`. The Hub writes the same alias to its own `projects.json` with `default_env` and `allowed_envs` from settings defaults.
- **FR-010** Audit event `source.project.registered` records the alias only, never the path. `scripts/check_docs.py` must pass for the new event and tool.
- **FR-011** Client rules (`docs/client-rules.md` with a new Version date, `docs/clients/templates/mcp-hub/SKILL.md`) say: ask the user when the Hub returns candidates; stop when a child reports a configuration error.

## Success criteria

- **SC-001** `scripts/package_qc.py` passes with coverage at or above the current gate, and `scripts/smoke.py` passes.
- **SC-002** A `hub__plan` call without a playbook never reaches a child (tests prove it).
- **SC-003** A search for an unregistered repository ends with `needs_registration` in under one second.

## Edge cases

- The token expires after `planTimeoutSeconds`-style TTL (reuse one setting or add `policy.confirmTokenSeconds`, default 600).
- A token is single use.
- The user answers with a different playbook than the one proposed: allowed, the Hub checks it against the candidates and the playbook rules.
- The path is a network share or a symlink: refused by the child, with a generic error.
- `source__register_project` is not in the Hub allowlist for read-only clients: it appears only in plans that declare it in `writes`.

## AGENTS.md check

- English only in code, docs, and tests.
- Functions of at most 50 lines; new behavior has tests for success and failure paths; tests run offline.
- Architecture: `policy` and `server` stay one-way; no extension import in `server`, `http`, `extensions`, `dashboard/api.py`, `jobs`.
- Tunables go in `settings.py` with docs in `docs/settings.md`.
- Docs change with code; `check_docs.py` and `check_leaks.py` pass; no drive paths in tracked files (use `<SET_BY_USER>` or `user@example.com`).
- The child change is in `../mcp-read-source`, under that repository's own rules; it is a separate commit there.

## Decisions

Agreed with the user. Do not re-discuss.

- Confirmation is enforced by the Hub, not by child MCPs. Default `always`.
- Repository registration is done when the user confirms, not by manual setup.
- Fast whole-repository search (ripgrep, count tool) is out of scope; it is a separate plan.
- BitNet is not used when the playbook is known.

## Out of scope

- Whole-repository search speed (ripgrep, count tool).
- Dashboard UI for playbooks.
- Changing the BitNet model or its timeout.

## Tasks

Track 1 (Hub, this repository):
- [x] T001 Tests first: confirmation (candidates returned, no plan stored; right token creates the plan; wrong, reused, expired token refused). (files: `tests/test_policy_confirm.py`)
- [x] T002 Implement `policy.confirmPlaybook` (FR-001, FR-002, FR-003, FR-004), with the token store in `policy.py`. (files: `src/mcp_hub/policy.py`, `src/mcp_hub/settings.py`, `docs/settings.md`)
- [x] T003 Skip BitNet when a playbook is given (FR-005). Test first. (files: `src/mcp_hub/server.py`, `tests/test_plan_assist.py`)
- [x] T004 Add `docs/playbooks/source-search.md` and `needs: source` (FR-006, FR-007). Test first. (files: `docs/playbooks/source-search.md`, `src/mcp_hub/playbooks.py`, `tests/test_playbooks.py`)
- [~] T005 (Hub part done: `NeedsRegistration` refusal and `hub.plan.needs_registration` audit; the registration tool and `source.project.registered` wait for the Open questions) Registration proposal `needs_registration` and the `source.project.registered` audit event (FR-008, FR-010). Test first. (files: `src/mcp_hub/policy.py`, `src/mcp_hub/audit.py`, `docs/security-and-logging.md`, `tests/test_policy_registration.py`)
- [x] T006 Client rules and skill template (FR-011). (files: `docs/client-rules.md`, `docs/clients/templates/mcp-hub/SKILL.md`, `docs/mcps/mcp-read-source.md`)

Track 2 (child, `../mcp-read-source`, separate commit and its own rules):
- [ ] T007 (blocked: see Open questions) Tests first: `source__register_project` validates the path and writes the alias; rejects drive roots, system folders, `..`. (files: `../mcp-read-source/tests/`)
- [ ] T008 (blocked: see Open questions) Implement `source__register_project` (FR-009). (files: `../mcp-read-source/src/...`)

Lead:
- [x] T009 Run QC and smoke in this repository; run the child's tests in its repository.
- [ ] T010 (waits for a Hub restart, which the user must allow) Live check: repeat the wec.be prompt, record the timeline from `logs/mcp-hub.jsonl`, and compare with the earlier 20 to 30 minutes.
- [ ] T011 (waits for the user's answer) Ask the user about the version, then commit each repository separately. Do not push.

## Analysis

| Requirement | Tasks |
|---|---|
| FR-001 to FR-004 | T001, T002 |
| FR-005 | T003 |
| FR-006, FR-007 | T004 |
| FR-008, FR-010 | T005, T007, T008 |
| FR-009 | T007, T008 |
| FR-011 | T006 |
| SC-001 | T009 |
| SC-002 | T001, T003 |
| SC-003 | T010 |

## Verification

- `uv run --no-sync python scripts/package_qc.py` and `uv run --no-sync python scripts/smoke.py` in this repository.
- The child's own test command in `../mcp-read-source`.
- Live: the wec.be prompt, timed from `logs/mcp-hub.jsonl`.

## Open questions

- **Child repository rule conflict (blocks T007 and T008).** `../mcp-read-source/AGENTS.md` says "Never add tools that write project files" and "Never open config/private/projects.json". Registration writes that file from a tool. The user must decide: (a) amend the child's rule for a config-only write with path checks, or (b) keep registration manual and the Hub only refuses with the fix.
- **Live check:** the running shared Hub still runs the old code; a restart is needed before T010, and it disconnects every AI client.
- **Smoke run:** two external checks failed during this change (OpenObserve "fetch failed"; SQL timeout), both in the Hub log, unrelated to the policy.

- Which `default_env` and `allowed_envs` should a registration get when none exists in settings? Proposed: `dev` and `dev, stg, prod` until the user changes them.

## Progress log

- 2026-11-13 Claude (lead): plan written from the user's approved proposal; the child repository change is a second track that needs its own commit.
- 2026-11-13 Claude (lead): Track 1 implemented: `plan_confirm.py` (offer, tokens), settings `policy.confirmPlaybook` and `confirmTokenSeconds`, gate in `server._create_plan`, BitNet skipped when a playbook is chosen, `source-search` playbook, audit events, docs, tests (`tests/test_plan_confirm.py`, `tests/test_policy.py` ConfirmGateTest). Track 2 blocked by the child's rule. QC passes; smoke fixed for the offer (`scripts/smoke.py`).

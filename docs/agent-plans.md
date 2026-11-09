# Agent plans

This page owns how a feature is planned with an AI agent and carried out by another agent. Other docs and [AGENTS.md](../AGENTS.md) link here.

Plans are **local only**: the `agent-plans/` folder is ignored by Git and never committed or pushed. Only this page is tracked. A plan may contain the user's private context, so do not copy its content into tracked files other than the code and docs it changes.

## Rules

- **One file per feature:** `agent-plans/<yyyy-mm-dd>-<feature-slug>.md`. A feature split across several agents uses a folder instead: `agent-plans/<yyyy-mm-dd>-<feature-slug>/00-overview.md` plus one `NN-track-<x>-<name>.md` per track. Each agent reads the overview and only its own track file, and changes only the files that track owns.
- **Spec Kit format:** write plans in Spec Kit structure, so every implementing agent follows the same standard:
  - user stories with priority and acceptance (`US1 (P1)`);
  - functional requirements (`FR-001`) and success criteria (`SC-001`);
  - edge cases, and a check against [AGENTS.md](../AGENTS.md);
  - tasks as `T001` with file paths and the tests to write first;
  - an analysis table that maps every requirement to its tasks.
- **Status** (first line under the title): `draft` (still being discussed), `approved` (the user agreed), `in-progress`, or `done`.
- **Approved means settled.** An agent given an `approved` or `in-progress` plan implements it as written. It does not re-open the `Decisions` section or propose another design. It asks the user only when the plan has a gap or contradicts the code, then records the answer in the plan (`Decisions` or `Open questions`).
- **Leave a trail.** While working, tick the task checkboxes and add one line per session to `Progress log` (date, agent, what was done, what is next), so the next agent continues where you stopped.
- **Writing a plan:** at the end of a planning discussion, the agent writes the plan from the template below and sets `draft`; it becomes `approved` only after the user says so.
- **Privacy:** the same rules as [AGENTS.md](../AGENTS.md#privacy-and-paths-tracked-files): repo-relative paths, placeholders, no secrets, no ticket, log, or tenant data.

## Template

```markdown
# <Feature name>

Status: draft

## Context

Why this change: the problem, what prompted it, the intended outcome.

## Decisions

Agreed with the user. Do not re-discuss.

- 

## Out of scope

- 

## Tasks

- [ ] <step> (files: `path/to/file`)

## Verification

- `uv run python scripts/package_qc.py`
- `uv run python scripts/smoke.py`

## Open questions

- 

## Progress log

- <yyyy-mm-dd> <agent>: <what was done; what is next>
```

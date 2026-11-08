# Agent plans

Feature plans agreed with the user in one AI agent session, written down so any other agent can carry them out without discussing them again.

## Rules

- **One file per feature:** `agent-plans/<yyyy-mm-dd>-<feature-slug>.md`, copied from [_template.md](_template.md).
- **Status** (first line under the title): `draft` (still being discussed), `approved` (the user agreed), `in-progress`, or `done`.
- **Approved means settled.** An agent given an `approved` or `in-progress` plan implements it as written. It does not re-open the `Decisions` section or propose another design. It asks the user only when the plan has a gap or contradicts the code, then records the answer in the plan (`Decisions` or `Open questions`).
- **Leave a trail.** While working, tick the task checkboxes and add one line per session to `Progress log` (date, agent, what was done, what is next), so the next agent continues where you stopped.
- **Writing a plan:** at the end of a planning discussion, the agent writes the plan here from the template and sets `draft`; it becomes `approved` only after the user says so.
- **Privacy:** the same rules as [AGENTS.md](../AGENTS.md#privacy-and-paths-tracked-files): repo-relative paths, placeholders, no secrets, no ticket, log, or tenant data.

# Track C: screens (actions, update card, headline, copy, fixture)

Read first: `00-overview.md` in this folder. You own only the files listed below. Do not commit.

## Files you edit

- `src/mcp_hub/desktop/app.py`
- `src/mcp_hub/desktop/update_card.py`
- `src/mcp_hub/desktop/screenshot.py`
- `src/mcp_hub/dashboard/locales/en.json`
- `src/mcp_hub/dashboard/locales/vi.json`
- new `tests/test_desktop_actions.py`

## Goal

FR-002 (headline line), FR-003 (one primary), FR-005 (emoji), FR-006 (titles and spacing), FR-007 (fixture).

## Tasks

- [ ] T005 Tests first in `tests/test_desktop_actions.py`:
  - in the stopped, running, and update-available states, exactly one primary button is visible and enabled;
  - only one of Start Hub and Open dashboard is visible at a time;
  - every card title uses the `section` role; "Updates" is not a different style;
  - `SAMPLE_RELEASE` in `screenshot.py` is higher than the running version;
  - the hint text in both locales has no emoji.
- [ ] T006 Implement:
  - in `app.py`, show only the action that fits the Hub state (Open dashboard while running, Start Hub while stopped); keep the disabled one hidden;
  - in `update_card.py`, make Update a plain `button(...)` unless it is the only actionable item (Hub running and an update available), then it is primary and Open dashboard is plain;
  - the headline mode caption moves to its own line, or is elided first, so the state word stays whole;
  - card titles use the `section` role; cards use `SPACE_M` between them;
  - `screenshot.py`: set `SAMPLE_RELEASE` above the running version the sample reports;
  - `en.json` and `vi.json`: `desktop.clients.hint` without the ⚙️ emoji, reading "Settings → Hub → Track AI clients" (and its Vietnamese equivalent) in both files.

## Rules

- Use only the existing `Row` and `button` APIs, plus track B's `Row.show_state` behavior. Track B changes `widgets.py`; you do not edit it. You do not depend on track D.
- Key both locales together: a key added or changed in `en.json` also goes in `vi.json`.
- Test offline: `uv run --no-sync python -m unittest tests.test_desktop_actions`. Qt offscreen: assert types, visibility, and properties, never pixels.
- Functions at most 50 lines, one purpose. English only in code and in `en.json`; Vietnamese only in `vi.json` values.
- Do not clamp anything the user can set; do not change `LIGHT`.
- If the `desktop.update.*` tests in `tests/test_desktop_update.py` break, note them in your report; the lead repairs them.
- No commits. Do not raise the version.

## Report back

The changed behavior per state, the test result, locale keys touched, and any request for a file you do not own.

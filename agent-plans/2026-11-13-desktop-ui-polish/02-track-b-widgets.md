# Track B: widgets (row elision, accessible names, switch factory)

Read first: `00-overview.md` in this folder. You own only the files listed below. Do not commit.

## Files you edit

- `src/mcp_hub/desktop/widgets.py`
- new `tests/test_desktop_rows.py`

## Goal

FR-002 (row elision), FR-004 (switch factory), FR-008 (accessible names).

## Tasks

- [ ] T003 Tests first in `tests/test_desktop_rows.py`:
  - a long Vietnamese state and detail are cut with "…" at the row width, and the tooltip holds the full text;
  - the tone icon has an accessible name equal to the state word;
  - `Page.switch_row` returns a `SwitchButton`, and `set_switch(switch, None, enabled=True)` disables it and shows it off.
- [ ] T004 Implement in `widgets.py`:
  - `Row.show_state(tone, text, detail="")`: elide the text and detail with the existing `show_elided`, and set the glyph's accessible name to the text;
  - `Page.switch_row(on_toggle) -> tuple[QLabel, SwitchButton]`, using the library's `SwitchButton` with On/Off texts from the locale keys `desktop.settings.on` and `desktop.settings.off`;
  - `set_switch(switch, value: bool | None, enabled: bool)`: `None` means off and disabled; otherwise set `checked` and enable by `enabled`.
  - Delete any helper this replaces that nothing else uses (check with grep first).

## Rules

- Contract: `Row.show_state(tone, text, detail="")`, `Page.switch_row(on_toggle)`, `set_switch(switch, value, enabled)`. Track C and D depend on these exact signatures.
- Ponytail: use the library's `SwitchButton` directly. No wrapper classes.
- Width comes from `theme.WINDOW_WIDTH`, which track A changes to 320. Do not hardcode 320 here; read the constant.
- Test offline: `uv run --no-sync python -m unittest tests.test_desktop_rows`. Qt offscreen has no fonts, so assert elided text and properties, never pixels.
- Functions at most 50 lines, one purpose. English only.
- If the existing segment helpers `segment` and `select_segment` are used by track D's files, leave them in place; only delete what nothing uses.
- No commits. Do not raise the version.

## Report back

The new function signatures, the test result, anything you deleted and why, and any request for a file you do not own.

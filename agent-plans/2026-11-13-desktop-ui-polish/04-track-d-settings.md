# Track D: settings (on/off switches)

Read first: `00-overview.md` in this folder. You own only the files listed below. Do not commit. Start only after track B reports done.

## Files you edit

- `src/mcp_hub/desktop/hub_page.py`
- `src/mcp_hub/desktop/settings.py`
- new `tests/test_desktop_switches.py`

## Goal

FR-004: Track AI clients, BitNet, Start Hub at sign-in, and Open this app at sign-in use the Fluent `SwitchButton`. Theme, language, glass, glass style, and the tabs stay segmented.

## Tasks

- [ ] T007 Tests first in `tests/test_desktop_switches.py`:
  - each switch calls its `on_*` callback with the new value (`on_tracking`, `on_bitnet`, `on_autostart`);
  - a switch with an unknown value (`None`) is disabled and shows off;
  - the segmented rows that remain (for example the glass style row in `settings.py`) still work.
- [ ] T008 Implement: replace `tracking_segments`, `bitnet_segments`, and `autostart_segments` in `hub_page.py` with switches built by track B's `Page.switch_row` and `set_switch`. Keep the `on_*` callbacks and their argument order unchanged. Remove the `_ON_OFF` segment helpers that nothing uses any more (grep first).

## Rules

- Contract (from track B): `Page.switch_row(on_toggle) -> tuple[QLabel, SwitchButton]`, `set_switch(switch, value: bool | None, enabled: bool)`. Read them in `widgets.py`; do not edit that file.
- Test offline: `uv run --no-sync python -m unittest tests.test_desktop_switches`. Qt offscreen: assert `checked` and `isEnabled()`, never pixels.
- Functions at most 50 lines, one purpose. English only.
- Keep the Hub's API calls and the callbacks as they are; this track changes presentation only.
- No commits. Do not raise the version.

## Report back

The list of switches replaced, the test result, the helpers deleted, and any request for a file you do not own.

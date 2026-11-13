# Track A: theme (contrast, width)

Read first: `00-overview.md` in this folder. You own only the files listed below. Do not commit.

## Files you edit

- `src/mcp_hub/desktop/theme.py`
- `tests/test_desktop.py`

## Goal

FR-001 and the width part of FR-002.

## Tasks

- [ ] T001 Contrast test. Write it first in `tests/test_desktop.py`: for both themes, compute the WCAG contrast of `ink` and `muted` against the card composited over a white and a black desktop through the acrylic tint, at the default card opacity and tint. Print the measured ratios in your report. (Writing the test first means it fails or reports the gap before the fix.)
- [ ] T002 Implement in `theme.py`:
  - add `contrast(foreground: str, background: str) -> float` (WCAG ratio, `#rrggbb` inputs); make `_contrast_with_white` call it;
  - set `WINDOW_WIDTH = 320`;
  - raise the dark card default (`GLASS["dark"]["card_alpha"]`) and tint only as far as needed for 4.5:1;
  - change `DARK` tokens only if the ratios still fail after that;
  - never edit `LIGHT`.

## Rules

- Contract: `theme.contrast(foreground, background) -> float`, `theme.WINDOW_WIDTH == 320`. Other tracks rely on these names.
- Test the offline way: `uv run --no-sync python -m unittest tests.test_desktop`. Never write to `config/private/` or `logs/`.
- Functions at most 50 lines, one purpose. English only.
- If the `LIGHT` `muted` ratio is below 4.5:1 over the worst-case backdrop, stop and report the number. Do not edit `LIGHT`.
- If a test needs a file you do not own, stop and report it.
- No commits. Do not raise the version.

## Report back

The measured ratios before and after, the changed constants, the test result, and any request for a file you do not own.

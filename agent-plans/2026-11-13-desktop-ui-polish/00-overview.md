# Desktop status app: UI polish from the ui-ux-pro-max review

Status: done

## Context

The Fluent conversion (`agent-plans/2026-11-13-desktop-fluent-ui.md`, released as 0.5.0) is done. A review with the `ui-ux-pro-max` skill (WinUI 3 / Fluent 2 rules, 20 `window.grab()` captures, light/dark, en/vi) found eight issues, F1 to F8. This plan fixes them with the least code, using several agents in parallel without file conflicts.

Facts that change the scope of the review findings:
- **F7 is a fixture bug only.** `update.py::_release_from` already rejects a release whose version is not higher than the running one, so the app never offers a downgrade. The "0.2.0" came from `screenshot.py::SAMPLE_RELEASE = (0, 2, 0)`. No card guard is needed.
- **F1 is unverified.** `window.grab()` has no acrylic backdrop, so the grey dark cards are not what a user sees. Measure the real composition before changing any value.
- `LIGHT` mirrors the dashboard's tokens and a test ties them, so this plan never edits `LIGHT`.

## User stories

- **US1 (P1)** I can read every line of text in both themes and both languages, on any wallpaper.
  - Acceptance: a contrast test passes for `ink` and `muted` on the card over the worst-case backdrop, in light and dark.
- **US2 (P1)** No word is cut mid-letter: a long text ends with "…" and its tooltip shows the full text.
  - Acceptance: a test with the longest Vietnamese state and detail finds no clipped label in the main view.
- **US3 (P1)** The window has one primary action at a time.
  - Acceptance: a test counts the primary buttons in the stopped, running, and update-available states, and finds exactly one.
- **US4 (P2)** On/off settings are switches, like in Windows 11 Settings.
- **US5 (P2)** The window looks consistent: one section-heading style, one spacing, no emoji in text.
- **US6 (P2)** I can use the window with the keyboard, and a screen reader names each status icon.

## Requirements

- **FR-001 (F1, US1)** Measure first: a script prints the contrast of `ink` and `muted` on the card composited over a white and a black desktop, through the acrylic tint, for each theme and each default opacity. Then raise only the dark card default and the tint (`theme.GLASS`), and `DARK` tokens if needed, until every ratio is at least 4.5:1. Never change `LIGHT`. The Settings slider keeps its full range: the floor applies to the defaults only.
- **FR-002 (F2, US2)**
  - `Row.show_state` cuts the name and the detail with "…" to the room they have, and puts the full text in the tooltip, reusing `widgets.show_elided`.
  - `WINDOW_WIDTH` goes from 288 to 320.
  - The Hub row keeps the state word whole; the mode caption ("fast mode") is cut before it, or moves to its own line.
- **FR-003 (F3, US3)**
  - Exactly one of **Start Hub** and **Open dashboard** is shown, depending on the Hub state. This amends the "disabled, never hidden" rule for this mutually exclusive pair; the docs change with it.
  - **Update** is a plain `PushButton` unless it is the only actionable thing (Hub running, update available), when it is the primary and **Open dashboard** is plain.
- **FR-004 (F4, US4)** Track AI clients, BitNet, Start Hub at sign-in, and Open this app at sign-in use the Fluent `SwitchButton`. Theme, language, glass, glass style, and the tabs stay segmented. A switch's text and enabled/unknown handling match what the segments did now.
- **FR-005 (F5, US5)** The `desktop.clients.hint` text has no emoji, in `en.json` and `vi.json`, and reads "Settings → Hub → Track AI clients".
- **FR-006 (F6, US5)** Every card title uses the `section` role ("Updates" included; its version line is body text), and the cards are separated by one spacing value, `SPACE_M`.
- **FR-007 (F7, US5)** `SAMPLE_RELEASE` in `screenshot.py` is higher than the running version.
- **FR-008 (F8, US6)** Each `Row` status icon has an accessible name taken from its state word, and the Tab order of the main view follows the visual order.
- **SC-001** `package_qc.py` (coverage 85% or more) and `smoke.py` pass.
- **SC-002** New captures of all 20 states, in `grab_polish/`, show no cut word, one primary, and readable text.
- **SC-003** The user confirms the glass and the dark theme on a real screen.

## Edge cases

- The Hub is `starting`, `unauthorized`, or `not_installed`: the one visible action button must still make sense (Start Hub while it cannot run is the primary; none is wrong).
- The user sets the card opacity slider below the default: the text may be less readable by choice; do not clamp the slider.
- Vietnamese is about 30% longer than English: every string must hold at 320 px.
- Fluent widgets are as wide as their label: rows of buttons need room for the longest label in both languages.
- A `SwitchButton` whose value is unknown (the Hub is not running) is disabled and shows off, like the segments did.
- Qt offscreen has no fonts: tests assert types, properties, and elided text, never pixels.

## AGENTS.md check

- English only; new locale keys go to `en.json` and `vi.json` together (this plan adds none beyond reusing the on/off keys).
- Functions of 50 lines or fewer, one purpose; every changed module has tests; tests run offline and never write `config/private/` or `logs/`.
- `desktop` imports nothing from `mcp_hub` outside itself; no blocking work in the tool-call path (not touched).
- The docs change with the code (`docs/desktop-app.md` Design keywords); `check_docs.py` passes. `check_leaks.py` passes: no drive paths or private paths in the plan files or notes.
- Never raise the version without the user's answer; the lead asks after QC passes.

## Decisions

Agreed with the user. Do not re-discuss.

- Scope is F1 to F8 of the review. F9 (icon button size) needs no change.
- Ponytail: change tokens and factories, use library widgets directly (`SwitchButton`), add no wrapper classes and no options, and delete what a change replaces.
- Acrylic stays; Mica is out of scope.
- The slider range is not clamped; defaults carry the readability guarantee.
- Plan files are English; chat with the user is Vietnamese.

## Out of scope

- The Hub dashboard (web), Mica, `FluentWindow`, a new color system, any change to `LIGHT`, the BitNet and update logic.

## Multi-agent execution

The lead (the main session) runs the tracks through `Agent` calls, so independent tracks run at once. Rules:

1. **One owner per file.** A track edits only the files in its table. A change another track's file needs goes to the lead as a request in the track's final report; it never edits the file.
2. **Same checkout, no worktrees.** Tracks have disjoint files, and a worktree would need its own `.venv` while the service locks the main one. Each track runs only its own tests with `uv run --no-sync python -m unittest <module>`. Only the lead runs the full QC, and only the lead commits.
3. **Contracts first.** The API a later track depends on is fixed here (see Contract), so tracks code against it in parallel.
4. **Waves.** Wave 1 runs tracks A, B, and C in parallel. Wave 2 runs track D after B reports done. Wave 3 is the lead: integration, existing-test repairs, QC, captures. Wave 4 is an independent read-only reviewer agent that gets the diff, not the lead's conclusions.
5. **Each agent prompt contains:** the overview path and its own track file path (it reads only those two plus the files it owns), the file-ownership rule, the contract, "write each test first", "stop and report instead of widening scope", and "no commits".
6. **Stop conditions.** A track stops and reports when its tests need a file it does not own, when a contract does not fit the code, or when FR-001's measurement shows `muted` on `LIGHT` below 4.5:1 (an open question for the user, not a reason to edit `LIGHT`).
7. **Failure handling.** If a track fails or reports a blocker, the lead fixes the contract or the file split, then re-runs only that track.

### File ownership

| Track | Plan file | Files it edits | Findings |
|---|---|---|---|
| A theme | `01-track-a-theme.md` | `src/mcp_hub/desktop/theme.py`, `tests/test_desktop.py` | F1, width |
| B widgets | `02-track-b-widgets.md` | `src/mcp_hub/desktop/widgets.py`, new `tests/test_desktop_rows.py` | F2 row, F4 factory, F8 |
| C screens | `03-track-c-screens.md` | `app.py`, `update_card.py`, `screenshot.py`, `en.json`, `vi.json`, new `tests/test_desktop_actions.py` | F2 headline, F3, F5, F6, F7 |
| D settings | `04-track-d-settings.md` | `hub_page.py`, `settings.py`, new `tests/test_desktop_switches.py` | F4 |
| Lead | this file | `docs/desktop-app.md`, existing test repairs, plan files, QC, captures, commit | docs, integration |

### Contract

- Track A: `theme.contrast(foreground, background) -> float` (WCAG ratio; `_contrast_with_white` calls it), `theme.WINDOW_WIDTH = 320`, and the glass defaults that pass FR-001.
- Track B: `Row.show_state(tone, text, detail="")` elides both with tooltips and sets the icon's accessible name. `Page.switch_row(on_toggle) -> tuple[QLabel, SwitchButton]` and `set_switch(switch, value: bool | None, enabled: bool)` in `widgets.py`; `checked` is the value; `None` is off and disabled.
- Track C uses only the existing `Row` and `button` APIs plus B's `Row` behavior; it needs nothing from D.
- Track D uses B's `switch_row` and `set_switch`, and replaces `tracking_segments`, `bitnet_segments`, and `autostart_segments` with switches, keeping the `on_*` callbacks unchanged.

## Tasks

Track tasks are in the track files. Lead tasks:

- [ ] T009 Baseline: QC run before wave 1 (log in the scratchpad); the existing `grab_after/` captures are the baseline.
- [ ] T010 Wave 1 (A, B, C in parallel), review each report, then wave 2 (D).
- [ ] T011 Integration: repair existing tests that used the segment helpers or the old widths; update `docs/desktop-app.md` (one primary shown or hidden, switches, width 320, glass defaults and measured ratios, accessible names).
- [ ] T012 Verify: `package_qc.py`, `smoke.py`, new captures into `grab_polish/` and a side-by-side check against `grab_after/`.
- [ ] T013 Independent review agent (read-only) on the diff; fix what it confirms.
- [ ] T014 Ask the user about the version (suggest 0.5.1), then commit and push; set the plan `done`.

## Analysis

| Requirement | Tasks |
|---|---|
| FR-001 | A: T001, T002 |
| FR-002 | A: T002 (width); B: T003, T004; C: T005, T006 |
| FR-003 | C: T005, T006 |
| FR-004 | B: T003, T004; D: T007, T008 |
| FR-005 | C: T005, T006 |
| FR-006 | C: T005, T006 |
| FR-007 | C: T005, T006 |
| FR-008 | B: T003, T004; C: T005 |
| SC-001 | Lead: T012 |
| SC-002 | Lead: T012 |
| SC-003 | Lead: T014 (the user's check) |

## Verification

- Per track: `uv run --no-sync python -m unittest <its test modules>`.
- Lead: `uv run --no-sync python scripts/package_qc.py` and `uv run --no-sync python scripts/smoke.py`.
- Captures: the scratchpad `grab_states.py` into `grab_polish/`, compared with `grab_after/`.
- The user: `python -m mcp_hub.desktop` on a real screen in dark and light, with glass, in Vietnamese.

## Open questions

- If `muted` on `LIGHT` is below 4.5:1 over the worst-case backdrop, what do we do? `LIGHT` mirrors the dashboard, so the choice is the user's: accept 3:1 for captions, or change the dashboard's token too. Track A reports the number.

## Progress log

- 2026-11-13 Claude (lead): plan approved; baseline QC passed before any change; waves 1 and 2 done (tracks A to D); 
  user chose the darker LIGHT muted token (#3e454e, dashboard matched) and a card alpha of 0.82 in dark; 
  test repairs done; BitNet action made plain; child rows give the state word half the row; QC and smoke pass; 
  captures in grab_polish/; read-only review done (typed findings fixed, the MCP rows moved to switches as the user chose); 
  version 0.5.1 (pyproject.toml and uv.lock); QC and smoke pass; committed, not pushed.

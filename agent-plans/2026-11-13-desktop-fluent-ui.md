# Desktop status app: Fluent UI with PySide6-Fluent-Widgets (desktop app under GPL-3.0)

Status: in-progress

## Context

The mcp-hub Status UI looks poor. It uses Unicode glyphs from mixed fonts, `●` is the glyph for both `ok` and `error`, the buttons are flat and web-like, there is no keyboard focus ring, and the app does not follow the Windows accent color. The user chose the most-starred library from the shortlist: zhiyiYo/PyQt-Fluent-Widgets (8,167 stars). It is GPL-3.0; the PySide6 build is `PySide6-Fluent-Widgets`, imported as `qfluentwidgets`. The user accepted relicensing the **desktop app** to GPL-3.0.

Facts checked:
- The repo is MIT (`LICENSE`, `README.md` → License).
- `desktop` imports nothing from `mcp_hub` outside itself (`tests/test_desktop_client.py`) and talks to the Hub only over HTTP.
- The library depends on `PySide6` (full package; the repo uses `PySide6-Essentials`), `PySideSix-Frameless-Window>=0.8.0` (GPL-3.0), and `darkdetect`.
- Almost every widget is created by the factories in `src/mcp_hub/desktop/widgets.py`: `label`, `button`, `ConfirmButton`, `Card`, `Row`, `Spinner`, `Page`, `FadingLog`.

Outcome: the app looks and behaves like a Windows 11 app, state is still readable in one second, tests still run offline, and licensing is explicit.

## User stories

- **US1 (P1)** As a user, I see a Windows 11-style window: Fluent cards, buttons, typography, icon-only buttons with tooltips.
  - Acceptance: before/after `--screenshot` captures (light/dark, en/vi) show Fluent widgets; no Unicode glyph buttons remain.
- **US2 (P1)** As a user, I tell states apart without relying on color. Each tone has its own icon shape.
  - Acceptance: `ok`, `wait`, `off`, `error` show four different icons, each with its state word.
- **US3 (P2)** As a user, the app follows my Windows accent color and works with the keyboard.
  - Acceptance: primary buttons and switches use the system accent, falling back to `blue`; Tab shows a focus ring.
- **US4 (P1)** As the maintainer, the license of what I ship is unambiguous.
  - Acceptance: the installer shows the GPL-3.0 license page and ships third-party notices; the README states Hub = MIT, app = GPL-3.0.

## Requirements

- **FR-001** Add `PySide6-Fluent-Widgets` as a dependency and lock it. Free widgets only (no "Pro").
- **FR-002** `theme.py` drives `setTheme` and `setThemeColor` (system accent via `QPalette.ColorRole.Accent`, else `blue`). `LIGHT` and `DARK` stay unchanged (a test ties them to the dashboard). The hand-written stylesheet keeps only the window background, the glass card fill, and tone colors.
- **FR-003** `widgets.py` factories return Fluent widgets:
  - `label` → `SubtitleLabel` / `StrongBodyLabel` / `BodyLabel` / `CaptionLabel`;
  - `button` → `PushButton` / `PrimaryPushButton`; small icon button → `TransparentToolButton(FluentIcon.*)` with a locale tooltip;
  - `Card` → `SimpleCardWidget`;
  - `Spinner` → `IndeterminateProgressRing`.
- **FR-004** Tone icons: `ok` → `InfoBarIcon.SUCCESS`; `wait` → `InfoBarIcon.WARNING` or the progress ring while busy; `error` → `InfoBarIcon.ERROR`; `off` → muted outline circle. `dot_icon` (taskbar) unchanged.
- **FR-005** Settings: `SegmentedWidget` for tabs, theme, and language; `SwitchButton` for glass and each child MCP; Fluent `Slider` for opacity and tint. BitNet install percent uses a thin `ProgressBar`.
- **FR-006** Update card keeps its compact layout. Buttons become Fluent buttons; ⟳ becomes `FluentIcon.SYNC`; **What's new** becomes `HyperlinkButton`.
- **FR-007** The window keeps the native title bar and existing glass (`desktop/win32.py`). Never `FluentWindow` or frameless windows.
- **FR-008** Licensing:
  - add `src/mcp_hub/desktop/LICENSE` (GPL-3.0) and `THIRD-PARTY-NOTICES.md`;
  - `installer.iss`: set `LicenseFile=`, ship the notices;
  - `README.md`: License section;
  - `pyproject.toml`: `license = "MIT AND GPL-3.0-or-later"`, choosing `-only` or `-or-later` to match the library's LICENSE.
- **SC-001** `package_qc.py` (coverage ≥ 85%) and `smoke.py` pass; `Desktop release` workflow green.
- **SC-002** Installer grows by at most ~25 MB compared with 0.4.1.
- **SC-003** Tests run on Qt offscreen on Windows and Linux CI.

## Decisions

Agreed with the user. Do not re-discuss.

- Use PyQt-Fluent-Widgets (most starred). Only the desktop app and installer become GPL-3.0; the Hub stays MIT.
- Keep the native title bar and our glass. The library is used only for widgets inside the window.
- Keep the four-tone meaning, "shape + color + text", one primary action, locale-only strings, no network work on the Qt thread.
- Code follows **ponytail**: change the factories, not every call site; use library widgets directly, no wrapper classes; delete the stylesheet rules and glyph constants they replace; no new abstractions or options beyond FR-001 to FR-008.
- Spec Kit format per `agent-plans/README.md`. The repo has no `.specify/`, so `speckit-*` scripts are not used.
- Ask about the version before committing. Suggested: 0.5.0.

## Edge cases

- System accent unreadable (Windows 10, Linux CI) → `blue`.
- Glass off or unavailable → Fluent cards use the theme's solid surface.
- Qt offscreen has no Fluent fonts/icons → tests assert widget types and icon enums, not pixels or elided text.
- Live theme switch → `setTheme` must re-polish existing widgets; verify no stale colors.
- `ConfirmButton` armed state on a Fluent button → stays red, still needs two clicks within `CONFIRM_MS`.
- PyInstaller bundle → must include `qfluentwidgets` resources; verify by running the built exe.

## AGENTS.md check

- English only; UI text in `en.json` and `vi.json`; any new tooltip key added to both.
- Functions ≤ 50 lines; every changed module covered by offline tests.
- Architecture unchanged: `desktop` imports nothing from `mcp_hub`.
- Docs change with code (Design keywords, packaging); `check_docs.py` passes.
- No private paths or secrets in notices or docs; `check_leaks.py` passes.

## Out of scope

- The Hub dashboard (web) UI.
- Fluent "Pro" widgets, `FluentWindow`, frameless window, Mica.
- Relicensing the Hub, or any file outside `src/mcp_hub/desktop` and `packaging/desktop`, except README, notices, and `pyproject.toml`.

## Tasks

Write each test first.

- [ ] **T001 Spike (gate).** `uv add PySide6-Fluent-Widgets`; convert the Hub card; run desktop tests offscreen; build installer and compare size with 0.4.1. If SC-002 or SC-003 fails, or glass breaks, stop and report. (files: `pyproject.toml`, `uv.lock`, `src/mcp_hub/desktop/app.py`)
- [ ] **T002 Before screenshots.** Save `--screenshot` captures of every state to the scratchpad `before/`.
- [ ] **T003 Licensing tests.** Installer has `LicenseFile` and notices; README states both licenses. (files: `tests/test_bootstrap_scripts.py`)
- [ ] **T004 Licensing.** (files: `src/mcp_hub/desktop/LICENSE`, `THIRD-PARTY-NOTICES.md`, `packaging/desktop/installer.iss`, `README.md`, `pyproject.toml`)
- [ ] **T005 Theme tests.** `setTheme` follows light/dark; accent from palette with `blue` fallback; stylesheet keeps only glass and tone rules. (files: `tests/test_desktop.py`)
- [ ] **T006 Theme bridge.** (files: `src/mcp_hub/desktop/theme.py`)
- [ ] **T007 Factory tests.** Factories return Fluent types; icon buttons have locale tooltips; `ConfirmButton` still needs two clicks; each tone maps to its icon. (files: `tests/test_desktop_window.py`, `tests/test_desktop.py`)
- [ ] **T008 Factories, Row, Spinner, Card.** Delete replaced glyph constants and stylesheet rules. (files: `src/mcp_hub/desktop/widgets.py`, `theme.py`)
- [ ] **T009 Screen tests.** Settings uses segmented/switch/slider and toggles still call `on_toggle`; update card buttons work; mini bar works. (files: `tests/test_desktop_window.py`, `tests/test_desktop_update.py`)
- [ ] **T010 Screens.** (files: `app.py`, `settings.py`, `hub_page.py`, `update_card.py`, `mini.py`)
- [ ] **T011 Docs.** Rewrite Design keywords for Fluent; add license note; update packaging notes. (files: `docs/desktop-app.md`, `docs/packaging-and-qc.md`, `docs/versioning.md`)
- [ ] **T012 Verify.** `package_qc.py` and `smoke.py`; after screenshots vs `before/`; run from source on HiDPI; build and install the installer.
- [ ] **T013 Release.** Ask about version, then commit, push, watch `Desktop release`.

## Analysis

| Requirement | Tasks |
|---|---|
| FR-001 | T001 |
| FR-002 | T005, T006 |
| FR-003 | T007, T008 |
| FR-004 | T007, T008 |
| FR-005 | T009, T010 |
| FR-006 | T009, T010 |
| FR-007 | T001, T008 (no window class change) |
| FR-008 | T003, T004 |
| SC-001 | T012, T013 |
| SC-002 | T001, T012 |
| SC-003 | T001, T012 |

## Verification

- `uv run --no-sync python scripts/package_qc.py`
- `uv run --no-sync python scripts/smoke.py`
- `uv run --no-sync python -m mcp_hub.desktop --screenshot <scratchpad>/after`; compare with `before/`.
- `uv run --isolated --group build python scripts/build_desktop.py`; install; GPL page shows; app runs.

## Open questions

- Is the library's license GPL-3.0-only or -or-later? T004 reads its LICENSE.

## Progress log

- 2026-11-13 Claude (Haiku 5.5): plan written to agent-plans after the user approved it; starting T001.
- 2026-11-13 Claude (Haiku 5.5), T001 partial: `pyside6-fluent-widgets` 1.11.3 added to `pyproject.toml` and `uv.lock` (`uv add --no-sync`). `uv sync` was blocked because `mcp-hub-service.exe` is running, so the package was installed with `uv pip install` into `.venv`; re-run `uv sync` once the service stops. The meta package `pyside6` pulls `pyside6-webengine` (125 MB wheel) and `pyside6-pdf` into the dev venv; the library imports only QtCore, QtGui, QtSvg, QtWidgets, QtXml at runtime. Desktop tests pass (209). Gate not measured yet: installer size needs the Hub card converted, and `scripts/build_desktop.py` needs Inno Setup 6 (not installed). Waiting for the user to choose how to continue.
- 2026-11-13 Claude (Haiku 5.5), T001 measured: Inno Setup 6 was already installed at `%LOCALAPPDATA%\Programs\Inno Setup 6`. Baseline installer (current code, no Fluent import) 33,654,086 bytes; spike with the Hub card's three icon buttons on `TransparentToolButton` 35,668,449 bytes, +1.9 MiB, under the 25 MB gate (SC-002 pass). WebEngine and Multimedia are not bundled; `Qt6Pdf.dll` is (candidate for an `excludes` entry). Desktop window and theme tests pass (117). Still open for T001: glass behaviour on a real screen (offscreen cannot show acrylic), which needs the user to look. Deviation: `FluentIcon` has no circle or error icon, so the `off` tone icon will be painted with QPainter (as `dot_icon` already is), not taken from `FluentIcon`. Spike icon buttons have no locale tooltips yet (T007).
- 2026-11-13 Claude (Sonnet 5.5 session), T002-T012 done except the real-screen checks: before images are in the scratchpad `grab_before/` (HEAD source, `grab()`); after images in `grab_after/`. Licensing: `src/mcp_hub/desktop/LICENSE` (GPL-3.0-only), `THIRD-PARTY-NOTICES.md`, `LicenseFile` and the notices in `installer.iss`, README License section. Build: `package_qc.py` exit 0 (coverage 91%), `smoke.py` 5/5 children, installer 35,673,955 bytes against 33,654,086 before (+1.9 MiB, SC-002 pass). Desktop tests: 221 pass in ~18 s (were ~236 s before the two fixes below).
- Deviations from this plan, each with its reason:
  - FR-003 labels: stay plain `QLabel` styled by roles. Fluent label classes restyle on every role change; with them the window tests took about 4 minutes.
  - FR-003 cards: stay `Card` (QFrame). `SimpleCardWidget` paints an opaque background, which hides the glass.
  - FR-003 Spinner: a painted quarter arc in the `wait` color, not `IndeterminateProgressRing` (it needs a widget in the glyph column).
  - FR-004 `off` icon: painted outlined ring; `FluentIcon` has no circle.
  - FR-005 segments: `SegmentedWidget` with route key `str(value)`. Selection is `setCurrentItem`, not `setChecked`, so callers changed.
  - "One primary action": Restart Hub in Settings is a plain button now; Open dashboard and Start Hub are both `PrimaryPushButton` and only the enabled one acts.
  - Refresh (⟳) moved beside the Child MCPs count; the action row needed the width for two Fluent buttons with full labels.
  - "What's new" moved to its own row under the buttons (Fluent button minimums do not leave room beside the title).
  - Update retry label shortened to one word in both locales (key `desktop.update.retry`): Fluent buttons are as wide as their text.
  - Accent: the Windows accent is used only when white text on it reads at 3:1 or better; otherwise `blue` (a pale accent `#db9ee5` was seen on the dev machine).
  - FR-008 `pyproject.toml` `license` field: not added (PEP 639 string needs setuptools 77+, and the build requires >=68). The license is stated in the LICENSE file, README, notices, and the installer.
  - License: `GPL-3.0-only`, not `or-later`: the library's LICENSE is the plain GPL-3.0 text. `PySideSix-Frameless-Window` is LGPL-3.0 per its package metadata (the plan said GPL).
- Open for the user: the glass on a real screen (acrylic cannot be checked offscreen; the screen capture in this session shows only the wallpaper), and a run from source on a HiDPI screen.

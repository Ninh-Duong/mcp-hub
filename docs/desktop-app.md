# Desktop status app

A small frosted-glass window (Qt), installed on its own with `mcp-hub-status-setup-<version>.exe`. It is a **viewer**: it reads the Hub over the Hub's HTTP API and never opens a file of the Hub or of a child MCP. It shows whether the shared Hub is running, whether each child MCP is connected, and, when the Hub tracks AI clients (the activity UI), which AI clients use it. Its Settings card holds the Hub's everyday options. When it starts it also checks GitHub once for a newer version of itself and offers an **Update** button ([Updates](#updates), [versioning.md](versioning.md)). This file is the reference for the app. Any change to the app follows it, and a change to the app's behavior or design updates it.

## Purpose and scope

- **Status.** The window shows the Hub state, the Hub mode (fast or activity UI), one row per child MCP, and, with the activity UI, the live AI clients plus those active in the last 30 minutes. With the activity UI, a child in use gets a short line saying which AI client is using it now, or used it in the last 2 minutes, and each AI client row shows the children it uses (see [Child usage line](#child-usage-line)).
- **Main actions.** **Start Hub** runs `hubctl start` in the background, without a window. **Open dashboard** pairs a browser session and opens `/dashboard/`, as `hubctl open` does. The ⟳ button polls at once.
- **Mini bar (⤡).** The ⤡ button in the Hub card shrinks the app to a one-line bar that stays above other windows: one dot per child (blue `◉` while an AI client calls it), the connected count, and the AI-client count; while the Hub is not running, the Hub's dot and state word instead. Drag it anywhere; ⤢ brings the full window back. The app reopens in the mode and at the place it was left (see [Design keywords](#design-keywords)).
- **Settings (⚙).** The ⚙ button in the Hub card opens the Settings card. It has three tabs:
  - **Appearance:** theme (Light/Dark), language (EN/VI), frosted glass on or off, card opacity (20–100 %), and background tint (0–90 %). These are remembered in the app's own `%APPDATA%\mcp-hub-status\desktop.json`. **Reset to defaults** turns glass on and restores the theme's tuned opacity and tint, keeping the theme and language. Without a saved choice, the app follows the Windows app theme and display language.
  - **Hub:**
    - Start, Restart, and Stop the Hub (`hubctl start`, `restart`, `stop`).
    - **Track AI clients**, the activity UI saved for every start (see [Hub settings](#hub-settings)).
    - **Start Hub at sign-in** (the Hub's task, changed through the Hub's API) and **Open this app at sign-in** (the app's own entry in the user's Run key; only in the installed app).
  - **MCPs:** each child MCP on or off.

  The Hub keeps these settings, so the Hub and MCPs tabs show them only while it runs (otherwise `desktop.settings.needsHub`). A change to the tracking mode or a child is saved by the Hub to `config/private/hub.json` and applies at the next Hub start; the card then shows **Restart Hub**.
- **Update card.** When a newer version of the app is available, a card above the actions says which version, which one is in use, and offers **Update** and **What's new**. It appears only in the installed app, only after the check at start, and only in the full window (see [Updates](#updates)).
- **Not in scope.** Jobs, write approvals, logs, tool allowlists, cache times, and every secret stay in the dashboard, `hubctl`, and the private files. That covers child credentials and `agents.json`. The app never shows tool arguments, results, tokens, or file paths.

## Install, run, disable

| Action | How |
|---|---|
| Get the installer | Download `mcp-hub-status-setup-<version>.exe` and its `.sha256` from the repository's latest GitHub Release, or run `.\scripts\hubctl.ps1 app -Install`, which downloads both, refuses a file that does not match its SHA-256, and runs the setup. `hubctl install` and `hubctl app` point to the release while the app is not installed. A private repository's release needs a signed-in download. |
| Install the app | Run `mcp-hub-status-setup-<version>.exe` (per user, no admin). Choose a desktop icon and **Open at sign-in**. |
| Open the app | Start Menu or desktop → **mcp-hub Status**, or `.\scripts\hubctl.ps1 app` |
| Run it from source (development) | `uv run python -m mcp_hub.desktop` |
| Turn opening at sign-in off / on | Settings → Hub → **Open this app at sign-in** (installed app only) |
| Turn the Hub's start at sign-in off / on | Settings → Hub → **Start Hub at sign-in**, or `.\scripts\hubctl.ps1 autostart -Target hub -State off` / `on` |
| Update | The installed app checks once at start and shows an **Update** button when a newer version is released (see [Updates](#updates)). Or run the setup of a newer version: it asks **Update** or **Close** and keeps the folder and the remembered look. A `/SILENT` run updates without asking. How versions and upgrades work: [versioning.md](versioning.md). |
| Run the setup again (same or older version) | It never installs again: it says the app is already installed and offers **Uninstall** or **Close**. A `/SILENT` run exits with code 1. |
| Uninstall | Run the setup again and choose **Uninstall**, or Windows Settings → Apps → **mcp-hub Status**. It removes the app, its shortcuts, its sign-in entry, and its remembered look; the Hub and its MCPs are not touched. |
| Save review screenshots of every state | `uv run python -m mcp_hub.desktop --screenshot <folder>` (see [Screenshot review](#screenshot-review)) |

- The installer puts the app in `%LOCALAPPDATA%\Programs\mcp-hub Status` and writes `HKCU\Software\mcp-hub-status\InstallPath`, which `hubctl app` uses. Without an installed app, `hubctl app` runs the source copy with the venv's `pythonw.exe`.
- The Hub no longer registers a task for the app. `hubctl install` removes the `mcp-hub-status` task that older versions registered.
- Only one copy runs. A second start finds the `Local\mcp-hub-status` mutex taken and exits; the installer and uninstaller use the same mutex to ask the user to close the app.
- Closing the window exits the app. It comes back at the next sign-in (when that is on) or from the Start Menu.
- The window has a fixed size that fits its content; it cannot be resized or maximized.
- The installed app never uses `.venv`, so `hubctl` never stops it. A copy run from source keeps Qt libraries in `.venv` open, so `hubctl` stops that copy before an environment sync (`Stop-StatusApp`) and reopens it afterwards (`Restore-StatusApp`).
- At sign-in the app can open before the Hub has started. It shows **Not running**, then **Running** at the next poll.

### Build the installer

```powershell
winget install JRSoftware.InnoSetup                  # once per build machine
uv run --isolated --group build python scripts/build_desktop.py # -> dist\mcp-hub-status-setup-<version>.exe
```

`scripts/build_desktop.py` draws the icon with Qt, bundles the app with PyInstaller (`packaging/desktop/mcp-hub-status.spec`, one folder, no console), checks that the bundle carries the Microsoft Visual C++ runtime (`BUNDLED_RUNTIME`), starts the bundled exe once in `--screenshot` mode as a self-check (a failure that happens only in the frozen app stops the build), and compiles `packaging/desktop/installer.iss` with Inno Setup. `--isolated` builds in a temporary environment, so it never syncs the shared `.venv`, whose entry-point exes the running Hub and AI-client bridges keep locked. The version is the one in `pyproject.toml`. Everything goes to `build/` and `dist/`, which git ignores; never commit them. The `build` dependency group holds PyInstaller and is synced only on request.

Before anything is installed, the setup:

- **Looks for an installed copy.** The same or a newer version stops setup (offering Uninstall); an older version is updated in place.
- **Shows a System check page.**
  - **Windows:** 10 version 1809 or later, 64-bit (`MinVersion`), which ships the Universal C runtime.
  - **Libraries:** Python, Qt and the Visual C++ runtime ship inside the app, so nothing else is installed.
  - **Disk space:** at least 300 MB free, or setup stops.
  - **The Hub:** whether its connection file exists. A missing file is only a warning; setup checks existence only and never reads the token.

The installer is not code-signed, so Windows SmartScreen may show "Unknown publisher" the first time (More info → Run anyway).

## Updates

How releases, numbering, and upgrades of the other parts work is in [versioning.md](versioning.md); this section is what the app does.

- **Check.** Once, on a worker thread, when the installed app starts: one `GET` for the latest Release of `UPDATE_REPOSITORY` (`constants.py`). It sends no token and nothing from the Hub. There is no switch, and a failed check shows nothing. An app run from source never checks.
- **Version.** The app reports the `mcp-hub` version it was built from (`importlib.metadata`; the spec bundles the metadata, and `build_desktop.py` checks `--version` against `pyproject.toml`). `python -m mcp_hub.desktop --version` prints it.
- **Health gate.** The Hub's live state stands in for `scripts/smoke.py`, which the installed app cannot run: a child that is `unavailable` or `reconnecting` blocks Update (a `FAIL`); `credentials required`, `disabled`, and `connecting` do not (a `WARN`); a Hub that is not running cannot be checked and does not block.
- **Install.** The worker downloads the installer and its `.sha256`, compares the SHA-256, and only then starts the hidden update script and closes the window. The script waits for the app to exit, runs the setup with `/SILENT /SUPPRESSMSGBOXES /NORESTART`, and opens the app again. Setup updates over an older version in place and keeps the folder, the sign-in entry, and the remembered look.
- **Trust.** The SHA-256 comes from the same Release, so it catches damage, not a changed Release; the installer is not code-signed (see [versioning.md](versioning.md#what-update-does)).

## Hub settings

The Hub and MCPs tabs call the Hub's settings API (`dashboard/settings_api.py`, see [Architecture](#architecture)). The Hub then changes `config/private/hub.json` through `src/mcp_hub/hub_settings.py`, the only writer of these values:

- **`servers.<id>.enabled`.** The MCPs tab lists the children found next to the Hub (`../<id>`) or listed in `hub.json`. It never offers a missing repository, which would stop the Hub from starting. A child whose private config still holds `<SET_BY_USER>` shows "needs credentials".
- **Top-level `extensions`.** `["activity-ui"]` keeps the activity UI on for every start, logon starts included, until it is turned off. The service starts the union of this list and the one-shot `launch.json` that `hubctl ... -Ui` writes. `load_config` ignores the key.

Writes are atomic and keep every other key as it is. Only booleans and the extension list are written, so `hub.json` stays free of secrets. With `MCP_HUB_CONFIG` set, the file belongs to the user: the API refuses changes (409) and both tabs show the values read-only.

Restart and Stop disconnect AI clients for a moment, so they need a second click within `CONFIRM_MS` (4 s); the armed button turns `red` and reads `desktop.settings.confirm`.

The sign-in switches run on a worker thread. **Start Hub at sign-in** calls `GET|POST /dashboard/api/settings/autostart`; the Hub runs `hubctl autostart`, which owns the scheduled task. **Open this app at sign-in** is the `mcp-hub-status` value under `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`, written by the app (`desktop/system.py`) and by the installer; running from source the switch is disabled with `desktop.settings.hub.autostartAppSource`.

## Architecture

The app is a client of the shared HTTP service. **It imports nothing from `mcp_hub` outside `mcp_hub.desktop`** (`tests/test_desktop_client.py` checks every import), so the installed bundle holds only the app and Qt, and the app cannot reach a Hub module or file. Core modules never import it. The view uses Qt (`PySide6-Essentials`: QtCore, QtGui, QtWidgets only).

What the app may touch:

| Thing | How |
|---|---|
| Where the Hub listens | The connection file the Hub writes at every start (below). Read only. |
| Status, activity, settings, restart, stop | The Hub's HTTP API on `127.0.0.1`, with the admin token from the connection file. |
| Start Hub | `Start-ScheduledTask mcp-hub` (`system.start_hub`): the Hub's own task, exactly as at sign-in. A stopping Hub closes its port at once but its task runs on while it drains, and the task ignores a new run until then; so the start first waits up to `DRAIN_WAIT_SECONDS` for that instance to end. |
| Its sign-in entry, its remembered look | The user's Run key value `mcp-hub-status`, and `%APPDATA%\mcp-hub-status\desktop.json`. |
| A newer version of itself | Only the installed app, only `https://api.github.com` and this repository's Releases on `https://github.com`, with a connection of its own that never carries the Hub's token (`update.py`, see [Updates](#updates)). The installer is kept in `%TEMP%\mcp-hub-status-update` until it has run. |

The **connection file** is `%LOCALAPPDATA%\mcp-hub\connection.json`: `{"version": 1, "url": "http://127.0.0.1:8765", "token": …}`. The service writes it at every start (`service_config.write_connection_file`). Its `token` is the Hub's **admin token**, new at every start and stored nowhere else; the AI clients' token in `service.json` cannot reach the dashboard API (see [security-and-logging.md](security-and-logging.md)). The file lives in the user's private app-data folder and is restricted to the user and SYSTEM. `MCP_HUB_CONNECTION_FILE` overrides the path; with `MCP_HUB_PRIVATE_DIR` set (tests) it is `connection.json` in that folder, so tests never replace the real one. Never open, print, or copy it.

| Module | Role |
|---|---|
| `desktop/usage.py` | Logic, no Qt. `stream_usage` (a worker thread) reads `GET /dashboard/api/activity/stream` with the bearer and forwards its snapshot and call events. It reconnects with a backoff from 1 to `STREAM_RETRY_MAX_SECONDS` (30 s). `UsageBoard` keeps the running calls and the last finished call per child; `usage()` gives a child's line and `client_usage()` the children each client uses. |
| `desktop/connection.py` | Reads the connection file: `load_connection` accepts only a `127.0.0.1` URL and a token of at least 32 characters, and raises `ConnectionMissing` (shown as **Not installed**) otherwise. The token is never in an error or `repr`. |
| `desktop/hub_api.py` | `HubClient`: the Hub's settings API (`read_settings`, `set_activity_ui`, `set_server_enabled`, `read_hub_autostart`, `set_hub_autostart`) and `control` (restart or stop). It holds the app's own `HubSettings` and `ServerSwitch` dataclasses. Failures are `OSError` or `ValueError`. |
| `desktop/system.py` | What the app does on Windows itself: `start_hub` (the Hub's scheduled task, no window) and its own Run-key entry (`app_autostart`, `set_app_autostart`, only when frozen). |
| `desktop/status.py` | Logic, no Qt. It reads the URL and admin token from the connection file. It calls `GET /health`, then `GET /dashboard/api/status` with the bearer, and builds a `HubSnapshot`. `open_dashboard` sends `POST /dashboard/pair` and opens the link. Requests go only to the `127.0.0.1` URL in the connection file, bypass any configured proxy, and time out after 3 s. |
| `desktop/i18n.py` | `Translator`: text from the `desktop.*` keys (plus the shared `connection.*` and `clients.empty` keys) in `dashboard/locales/<lang>.json`; `system_language` reads the Windows display language. |
| `desktop/theme.py` | Every design token: the `LIGHT` and `DARK` palettes, the `GLASS` values per theme, tone tokens and glyphs, spacing, sizes, fonts, timings. `stylesheet(theme, glass)` builds the one Qt stylesheet; `backdrop_tint(theme)` gives the acrylic tint. |
| `desktop/widgets.py` | `Card` (the frosted panel, styled by `QFrame#card`), `Row` (tone glyph, name, detail), `Page` (a Settings tab, with segment and slider rows), `ConfirmButton` (two-step confirm), `button`, `label`, `set_props` (stylesheet properties), and `dot_icon`. |
| `desktop/mini.py` | `MiniBar`, the pinned one-line bar of compact mode: dots updated in place, drag to move (`startSystemMove`). |
| `desktop/settings.py` | `SettingsPanel`, the Settings card (title, tab row, one visible page, restart notice), and `AppearancePage`. Pages report choices through callbacks and show the current ones through `sync`; `StatusWindow` applies and saves them. |
| `desktop/hub_page.py` | `HubPage` (Hub control, Track AI clients, the two sign-in switches) and `ChildrenPage` (one On/Off row per child). Both show `desktop.settings.needsHub` while the Hub is not running. |
| `desktop/update.py` | Logic, no Qt. `check_latest` (one request to the latest Release; any failure is `None`), `update_blocked` (the health gate), `download_verified` (the SHA-256 is compared before anything runs), `launch_update` (the hidden PowerShell that waits, installs silently, and reopens the app), `install`, and the `Updater` the window takes (a fake in tests). |
| `desktop/update_card.py` | `UpdateCard`: the card above the actions, with its note line (version in use, the gate, progress, or the one fixed failure sentence). |
| `desktop/win32.py` | Windows integration, each with a harmless fallback: the window style (title bar theme, rounded corners, acrylic accent), the single-instance mutex, the AppUserModelID shared with the installed shortcut (`set_app_id`), the Windows app theme, and screen capture. |
| `desktop/prefs.py` | Load and save the remembered choices in `%APPDATA%\mcp-hub-status\desktop.json`: `theme`, `language`, `glass` (bool), `glass_style` (`frosted` or `clear`), `card_opacity` (0–100), `tint` (0–90), `compact` (bool), and `compact_pos` (two ints). One rule per key; unknown keys and invalid or out-of-range values are dropped. |
| `desktop/screenshot.py` | The `--screenshot` review mode: sample snapshots and capture (the composed screen on Windows, `QWidget.grab` elsewhere). |
| `desktop/app.py` | `StatusWindow(QWidget)`, the view only: layout, incremental rendering, button handlers. Also the command line, `application()`, and `main`. |
| Hub side: `dashboard/settings_api.py` | `GET /dashboard/api/settings`, `POST …/settings/tracking`, `POST …/settings/servers/{id}` (body `{"enabled": bool}`; 400 for another body, 404 for an unknown child, 409 for a custom config), and `GET` or `POST …/settings/autostart` (the Hub's task only; 503 when `hubctl` fails). Bearer required; only booleans and child ids go in or out; file and `hubctl` work runs in `asyncio.to_thread`. |

Data flow: every `POLL_INTERVAL_MS` (5 s), a `QTimer` starts a daemon thread that runs `fetch_snapshot`. That function never raises: each failure maps to a Hub state. The thread puts the result on a queue. A second `QTimer` drains the queue on the Qt thread every `QUEUE_CHECK_MS` (200 ms) and renders. Open dashboard, the settings calls, and the sign-in switches also run on daemon threads (`StatusWindow.run_worker`) and report back through the same queue. So do Start, Restart, and Stop: the window shows the expected state at once, and a failed call (`control_failed`) drops it, shows `desktop.error.controlUnavailable`, and polls again. A start or restart that has not brought the Hub up within `START_GRACE_SECONDS` shows `desktop.error.startTimeout`. Both errors (`STICKY_ERROR_KEYS`) stay in the footer while the Hub is stopped, until it runs or another command is sent. When the Hub starts running while a settings tab is open, the tab loads its settings.

While the Hub runs with the activity UI, one more daemon thread listens to the activity stream (Server-Sent Events).
- It puts each message on the same queue, tagged with a generation number.
- When the Hub stops, the mode turns fast, or the window closes, the app sets the thread's stop event and bumps the generation, so messages already queued are dropped.
- The stream sends a keep-alive every 15 s, so the thread notices a stop request within that time. A read timeout of `STREAM_READ_TIMEOUT_SECONDS` (40 s) detects a dead connection.

### Incremental rendering

`render()` splits the window into sections: texts, Hub row, children, usage, clients, client usage, actions, settings, icon. The children and clients sections rebuild rows only when the child states or the client list change; the usage and client usage sections only set text on the existing rows. Each section has a key built from the data it shows, and a section is redrawn only when its key changed. A poll that reports the same state therefore changes only the footer time. Never rebuild rows or reset the icon on every poll: that made the first version flicker.

The outer layout uses `QLayout.SetFixedSize`, so the window follows its content without a manual resize. A theme switch applies one new stylesheet and redraws each section once (glyph tones, icon); it never rebuilds widgets.

### Glass

On Windows, `win32.style_window` styles the native window in three steps:
1. a dark or light title bar (`DWMWA_USE_IMMERSIVE_DARK_MODE`);
2. rounded corners (`DWMWA_WINDOW_CORNER_PREFERENCE`);
3. the acrylic accent (`SetWindowCompositionAttribute`, `ACCENT_ENABLE_ACRYLICBLURBEHIND`), tinted with the theme's `canvas` color at `GLASS[theme]["tint_alpha"]`.

The window has `WA_TranslucentBackground`, and with glass the stylesheet leaves the window background transparent. Cards are the theme's `surface` at `card_alpha`, with a light edge (`GLASS[theme]["border"]`). The blurred desktop shows through every card, and Qt draws text with correct alpha, so text stays crisp.

**Why not the documented backdrop.** A spike compared it with the accent above. `DWMWA_SYSTEMBACKDROP_TYPE` (acrylic) looked right only while the window was active: once another window took focus, Windows replaced the blur with a flat gray fallback. A status window spends most of its time inactive. The accent policy stays blurred in both states, so the app uses it, although the API is undocumented. If a Windows update breaks it, `set_acrylic` returns False and the app falls back to the opaque design below.

**Two glass styles.** Settings → Appearance → Glass style:
- **Frosted** (`frosted`, the default) uses `ACCENT_ENABLE_ACRYLICBLURBEHIND` (4). Windows adds a luminosity and noise layer, so text reads well over any background.
- **Clear** (`clear`) uses `ACCENT_ENABLE_BLURBEHIND` (3): a light blur without that layer, so the desktop shows through much more. With Card opacity at 0 % the cards are only their edge.

  Text stays fully opaque, but over a busy, bright background it is harder to read. That is the user's trade-off, and Frosted remains the default.

The tint slider applies to both styles.

**Fallback.** Where the acrylic accent is unavailable (it failed, other systems, the offscreen test platform), `glass` is False: the window background is the opaque `canvas` color, and cards are opaque `surface` with a `line` edge.

The title bar stays native (theme-colored, not blurred), for moving the window, the taskbar, and Alt+Tab.

## Design keywords

These are the rules for every UI change in the app. A change that breaks one updates this section first.

- **Calm, glanceable.** The state is readable in one second. Color signals state only and is never decoration. A connected child shows no state word (the green dot and the count say it); every other child state keeps its word.
- **Four status tones.** Every state maps to exactly one tone. Do not add a tone without updating this list, `status.TONES`, `theme.TONE_TOKENS`, and `theme.TONE_GLYPHS`.

  | Tone | Color token | Glyph | Meaning |
  |---|---|---|---|
  | `ok` | `green` | ● | Working |
  | `wait` | `amber` | ◐ | In progress, or checking |
  | `off` | `muted` | ○ | Turned off or not set up, not a failure |
  | `error` | `red` | ● | Needs attention |

- **Shape + color + text.** A tone always comes with its glyph and a state word, so it stays readable without color vision. Glyphs sit in a fixed-width column (`GLYPH_COLUMN_WIDTH`, 16 px), so names line up whatever the glyph.
- **Status icon.** The window and taskbar icon is a dot in the Hub's tone, so the state is visible while the window is in the background. It changes only when the tone changes.
- **Frosted glass.** A narrow window (`WINDOW_WIDTH` 288 px) holds four rounded cards (`CARD_RADIUS` 12): Hub, Child MCPs, AI clients, actions. Cards are translucent `surface` (`GLASS[theme]["card_alpha"]`) with a light 1 px edge, over the blur of whatever is behind the window (Frosted or Clear style). Text and glyphs are always fully opaque. The window fits its content and cannot be resized or maximized. The native title bar stays.
- **Cards carry all content.** Every label, button, and note sits inside a card; the space between cards is pure glass. A list card has a small uppercase `muted` heading, with a count on the right: `2/4` connected children, or the number of live clients.
- **Two themes, one token set.** `LIGHT` mirrors the `:root` tokens of `dashboard/style.css`, and a test fails if they drift apart. `DARK` uses the same token names, with dark surfaces and brighter tone colors. Each theme has its own `GLASS` defaults: the light theme uses a lighter tint and a white edge, the dark theme a deeper tint and a faint edge. The user's opacity and tint (`theme.Appearance`) override them; `None` means the theme default, so switching themes keeps each theme's tuning until the user moves a slider. Colors reach widgets only through `theme.stylesheet` and the `role` / `tone` / `primary` / `small` properties; code never sets a color on a widget.
- **Spacing scale.** 4 / 8 / 12 / 16 px (`SPACE_XS` … `SPACE_L`), in Qt device-independent pixels (Qt scales them with the screen). No other spacing values.
- **Typography.** `Segoe UI Variable Text` (then `Segoe UI`) on Windows, the Qt default elsewhere, in four styles: headline 11 semibold (the Hub state), section heading 8 bold, body 10, caption 9 in `muted`. Tone glyphs use `Segoe UI Symbol`, so ● ◐ ○ render at one size.
- **One primary action.** The main view has exactly one `blue` button (`primary` property): **Start Hub** while the Hub is not running, **Open dashboard** while it runs. The Settings card adds a second one, **Restart Hub**, only while saved changes wait for a restart. Other buttons are secondary: a faint `ink` tint. A button that cannot act is disabled, never hidden. Small controls (⚙, ⟳, the Hub control buttons) use the `small` property.
- **Responsive controls.** Every button has hover, pressed, and disabled states from the stylesheet, a pointing-hand cursor, and `BUTTON_RADIUS` 8 corners.
- **Settings card.** The card is inline, right below the Hub card, never a second window, and the window grows to fit it.
  - The title and the tab row (Appearance | Hub | MCPs) share the first line. One tab shows at a time, so the card stays short.
  - Each row is a caption on the left and a control on the right. Choices are segmented buttons (`segment` property; the checked one is `blue`). Amounts are sliders with their value in percent.
  - A control that cannot act is disabled: the sliders while glass is off, glass where it is unavailable (with a caption), values still loading (with `desktop.settings.loading`), and every Hub setting with `MCP_HUB_CONFIG` set.
  - Appearance changes apply live; a save follows `PREFS_SAVE_DELAY_MS` after the last change, or on close. Hub settings are saved at once and apply at the next start.
- **Confirm before disconnecting clients.** Restart and Stop need a second click within `CONFIRM_MS`; the armed button is `red`. Nothing else in the app interrupts AI clients.
- **Bilingual by data.** Every visible string comes from a locale key. The only literal glyphs are the tone, settings, refresh, and usage (`↳`) glyphs in `theme.py`, and the language codes (`EN`, `VI`).
- **Never block, never leak.** Network work runs off the Qt thread. The app shows only state names and client names; never the token, private paths, or raw error text. A failed action shows one fixed sentence in the footer.
- **Mini bar.** Compact mode is one rounded card with no title bar, always on top, and dragged by its body.
  - While the Hub runs, left to right: one dot per child in its tone (`LIVE_GLYPH` ◉ in `blue` while an AI client calls it), the `ok/total` count, `desktop.mini.clients` with the activity UI, and the ⤢ button. There is no separate Hub dot, so the number of dots always matches the count.
  - While the Hub is not running, the bar shows the Hub's tone dot and its state word (e.g. `◐ Starting…`) instead of child dots.
  - Names and details are in tooltips (`desktop.mini.dot`), never on the bar.
  - Dots are updated in place and rebuilt only when the children change.
  - Switching modes recreates the native window, so the style is applied again.
- **Child usage line.** A short, caption-size line under the child's name, `↳ {clients} · {when}`, indented past the glyph column. It exists only while there is usage: an idle child is a single row, so the list keeps an even rhythm.
  - It is `blue` (`role="live"`) with `desktop.usage.now` while calls run, then `muted` with "just now" / "N min ago" for `RECENT_SECONDS` (2 min). Whole minutes keep the text from changing more than once a minute.
  - Several clients merge as `Codex ×2, Antigravity`.
  - The tool name is not on the line; the tooltip (`desktop.usage.detail`) carries it when all running calls use one tool.
  - The line stays 2 minutes after the last call, so the window grows once per burst of calls and shrinks once after it, not on every call.
- **Client usage detail.** An AI client row's right-hand detail names the children that client uses: `blue` while it calls them, `muted` for 2 minutes after, then back to its usual detail. One board feeds both cards, so they always agree.
- **Update card.** One card between the AI clients and the actions, shown only while an update is available and only in the full window (the mini bar never shows it). A headline names the version; a caption says which one is in use. **Update** is a secondary button, so **Open dashboard** or **Start Hub** stays the one primary action. A button that cannot act is disabled, never hidden: it waits while a download runs and while the health gate blocks (the caption then names the failing children). A failed update shows one fixed sentence in `red` and enables the button again. No version string, path, or error text from GitHub is shown.
- **Steady.** A refresh that finds nothing new must not move, resize, or redraw anything except the footer time (see [Incremental rendering](#incremental-rendering)). Only new usage may change the height.

## States

| `hub` | Tone | Text key | What the user does |
|---|---|---|---|
| `running` | `ok` | `desktop.hub.running` (+ `desktop.mode.fast` or `desktop.mode.activityUi`) | Nothing. Open dashboard is the primary action. |
| `starting` | `wait` | `desktop.hub.starting` | Wait. Shown for up to `START_GRACE_SECONDS` (120 s) after Start Hub while `/health` does not answer yet. |
| `stopped` | `error` | `desktop.hub.stopped` | Press Start Hub, or run `hubctl status`. |
| `not_installed` | `off` | `desktop.hub.notInstalled` | There is no valid connection file: the Hub never started for this user. Run `hubctl install`. |
| `unauthorized` | `error` | `desktop.hub.unauthorized` | The Hub refused the token (HTTP 401): run `hubctl restart`. |

Before the first poll the Hub row shows `desktop.checking` in the `wait` tone. While the Hub runs, the mode (`desktop.mode.*`) follows the state word in `muted`.

| Child state (from `runtime.child_states()`) | Tone | Text key |
|---|---|---|
| `connected` | `ok` | `connection.connected` |
| `connecting`, `reconnecting` | `wait` | `connection.connecting`, `connection.reconnecting` |
| `disabled` | `off` | `connection.disabled` |
| `credentials required`, `unavailable`, any other reason | `error` | `connection.credentialsRequired`, `connection.unavailable`, or the reason as reported |

### Child usage line

| Board state for the child | Line | Role |
|---|---|---|
| Calls running | `↳ Codex · using now`, or `↳ Codex ×2, Antigravity · using now`; tooltip `Codex · query_logs · using now` | `live` (`blue`) |
| Last call finished less than 2 min ago | `↳ Codex · just now` / `↳ Codex · 1 min ago` | `caption` (`muted`) |
| Otherwise, or fast mode | no line, no space | |

The AI CLIENT card reads the same board (`client_usage`): a client with running calls shows those children in `blue`; otherwise children it used in the last 2 minutes show in `muted`. Without either, the row keeps its usual detail (empty for a live client, `clients.recent` for a recent one).

The board comes from the stream:
- The first message is a snapshot: running calls with their client (`active_call_details[].client`), and the last call (`last_client`, `last_tool`, `last_at`).
- Then each `started` event adds a call, and each `succeeded` / `failed` / `interrupted` event ends it.
- A resync snapshot rebuilds the board.

Only server IDs, tool names, client labels, and times are used; never arguments or results.

AI clients: live clients come first, one row each in the `ok` tone, with `×N` for several connections of one client. Clients active in the last 30 minutes without a live connection follow in the `off` tone with `clients.recent`. The count is the number of live clients. In fast mode the card shows `desktop.clients.hint`, which points to Settings → Hub → Track AI clients. With the activity UI and no clients it shows `clients.empty`. While the Hub is not running, both list cards show `desktop.waitingForHub`.

## Locale keys

The app's own keys:
- `desktop.settings.{open,title,theme,themeLight,themeDark,language,glass,glassStyle,frosted,clear,on,off,cardOpacity,tint,reset,glassUnavailable}`
- `desktop.compact.{on,off}`, `desktop.mini.{clients,dot}`
- `desktop.settings.{confirm,pending,restartNow,loading,needsHub}`, `desktop.settings.tab.{appearance,hub,mcps}`
- `desktop.settings.hub.{control,start,restart,stop,tracking,trackingHint,autostartHub,autostartApp,autostartAppSource}`
- `desktop.settings.mcps.{empty,readOnly,credentials}`
- `desktop.section.{children,clients}`
- `desktop.checking`
- `desktop.hub.{running,starting,stopped,notInstalled,unauthorized}`
- `desktop.mode.{fast,activityUi}`
- `desktop.waitingForHub`, `desktop.children.empty`, `desktop.clients.hint`
- `desktop.button.{openDashboard,startHub}`
- `desktop.updated`
- `desktop.update.{available,current,button,whatsNew,downloading,installing,failed,blocked,hubNotChecked}`
- `desktop.error.{dashboard,controlUnavailable,startTimeout,settings,autostart}`
- `desktop.usage.{line,detail,now,justNow,minutesAgo}`

It also uses the dashboard's `connection.*`, `clients.empty`, and `clients.recent`.

Rules:
- Add a key to `en.json` and `vi.json` in the same change, and remove keys the app no longer uses.
- The language segment shows codes (`EN`, `VI`) rather than language names, so `en.json` never needs Vietnamese text.
- Text in code, tests, and this doc stays English. The only Vietnamese in the repository is the values in `vi.json`.

## Screenshot review

```powershell
uv run python -m mcp_hub.desktop --screenshot <folder> [--states running,ui,starting,stopped,not_installed,unauthorized,update,update-blocked,settings,settings-hub,settings-mcps,compact] [--lang en,vi] [--theme light,dark]
```

The command opens the window on top of other windows and renders fixed sample data for each listed state, language, and theme. The `settings`, `settings-hub`, and `settings-mcps` states are the running sample with that Settings tab open, filled with sample Hub settings. The `update` and `update-blocked` states are the running sample with the update card, without and with a failing child. The `compact` state is the `ui` sample as the mini bar. It saves `<state>-<language>-<theme>.png` for each combination, prints the paths, and exits.

- It never reads or changes the live Hub, `hub.json`, or the sign-in tasks, so a review never restarts anything.
- Every list defaults to all values.
- If `uv run` fails with `os error 32` on `mcp-hub-service.exe`, the environment needs a sync while the Hub runs. Run `hubctl install` once (it stops the Hub, syncs, and restarts it), or add `--no-sync` to `uv run`.
- On Windows the capture reads the screen as DWM composes it, so the images include the real blur. Elsewhere the window renders itself, without a backdrop.
- Keep the screenshot folder outside the repository, or in an ignored folder.
- The single-instance rule does not apply to this mode.

## Testing and review checklist

`tests/test_desktop.py` covers the logic, without Qt and with a mocked opener, browser, launcher, DWM, and `kernel32`:

- **Snapshot mapping:** every Hub state, the child tones, client merging, and malformed status fields.
- **Dashboard pairing:** a failure raises without opening a browser or showing the token.
- **Theme:**
  - `theme.LIGHT` equals the `style.css` `:root` tokens, and both palettes share token names;
  - the stylesheet is frosted only with glass;
  - the tint and `rgba` helpers.
- **Language:** the default choice, placeholders, and that every key the app uses exists in both locales.
- **Remembered choices:** the round trip of every key, invalid or out-of-range values and unknown keys dropped, broken or unwritable files ignored.
- **Appearance:** the user's opacity and tint in the stylesheet and the backdrop tint, the theme defaults, glass off giving the opaque stylesheet, and glass off sending `ACCENT_DISABLED`.
- **Windows integration:** the mutex; the title bar, corner, and acrylic calls and their fallback; the app theme; a real screen capture (Windows only).
- **`control`** (Hub side): the `launch_hubctl` and `run_hubctl` arguments, failures and timeouts as `OSError`, and the autostart JSON (the Hub's task only).

`tests/test_desktop_window.py` covers the window on Qt's `offscreen` platform (`QT_QPA_PLATFORM=offscreen`), so no window opens and the tests run on every OS, CI included:

- rows and counts, tone properties, and the primary button;
- starting/stopped transitions and footer errors;
- the buttons, the ⚙ toggle, and the Settings controls:
  - the theme and language segments;
  - the glass switch;
  - the sliders: a live restyle, and one save after a delay or on close;
  - controls disabled when glass is unavailable;
  - reset;
  - compact mode (flags, the mini bar dots and counts, live dots, tooltips, saving, restoring on a visible screen, dragging), the Clear glass style, and the hidden word for connected children;
  - the tabs, the Hub tab (tracking, the confirmed restart and stop, sign-in switches, no app switch from source), the MCPs tab, the read-only state, settings hidden while the Hub is stopped and loaded once it runs, a failed start, and errors in the footer;
  - the default actions: Start through the task, Restart and Stop through the API, and the combined sign-in state;
- a theme switch that restyles without rebuilding;
- the glass stylesheet and the icon;
- polling on a worker thread with bounded waits, and that an unchanged poll redraws nothing;
- screenshot mode, the command line, and `main`.

The tests skip with a reason only if Qt cannot load; the CI Linux job installs the system libraries Qt needs.

`tests/test_desktop_usage.py` covers the SSE parser, the usage board (running and recent calls, merging, expiry, minute steps, snapshots, bad events), opening the stream with the bearer without showing it, and the worker's forwarding, backoff, and stop. `tests/test_desktop_window.py` also covers the usage lines and the client details: live updates without rebuilding rows, a hidden line giving its space back, the stream following the mode, stale messages dropped, and closing.

`tests/test_desktop_client.py` covers the client side: bad connection files (never showing the token), `HubClient` calls with the bearer and their failures, `start_hub`, the Run-key entry, `set_app_id`, and **the import boundary** (no `mcp_hub` module outside `desktop`).

`tests/test_settings_api.py` covers the Hub's settings API: the payload, the bearer requirement, changes, bad bodies, unknown children, a custom config, unreadable settings, and the Hub's sign-in task. `tests/test_service_config.py` covers the connection file: written once, read back by the app, its path overrides, and the service logging only the error type.

`tests/test_hub_settings.py` covers the `hub.json` writes: kept keys, a missing file, refused children, the saved mode, credentials, the read-only explicit config, and failed writes.

`tests/test_bootstrap_scripts.py` covers:
- that `hubctl install` removes the old `mcp-hub-status` task, and `hubctl app` opens the installed exe or else runs from source;
- `hubctl autostart`, which switches only the Hub's logon trigger and is kept by `hubctl install`;
- that an environment sync stops a copy run from this checkout first and reopens it afterwards;
- the packaging: the spec's relative paths and locales, the per-user installer and its sign-in cleanup, the build script's commands and icon, and that `build/` and `dist/` are ignored.

Before you finish a change to the app:

1. Run `uv run python scripts/package_qc.py`. It covers English-only text, functions of at most 50 lines, and a test for every module.
2. Make sure the code uses no color, size, or spacing literal outside `theme.py`, and no visible string outside the locales.
3. Run the screenshot review for every state, both languages, and both themes. Check each image for alignment, clipping, contrast, and exactly one primary button. Put a colorful window behind the capture spot to judge the glass.
4. Run the real app (`uv run python -m mcp_hub.desktop`) for a minute against the running Hub. Check that it stays still between polls, and that it stays frosted while another window has focus.
5. When the change touches the app's files, its imports, or `packaging/desktop/`, build and install the installer (see [Build the installer](#build-the-installer)) and open the installed app once.
6. Update this doc when you add a state, key, tone, token, or action.

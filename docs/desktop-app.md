# Desktop status app

A small frosted-glass window (Qt), installed on its own with `mcp-hub-status-setup-<version>.exe`. The app and its installer are GPL-3.0-only, because the app uses PySide6-Fluent-Widgets (GPL-3.0); the Hub stays MIT. See [src/mcp_hub/desktop/LICENSE](../src/mcp_hub/desktop/LICENSE) and [THIRD-PARTY-NOTICES.md](../THIRD-PARTY-NOTICES.md). It reads Hub state over HTTP and reads its own preferences and the sanitized update state file under user app data. It shows whether the Hub and child MCPs are working, and, when enabled, which AI clients use them. Its Settings card holds the Hub's everyday options. It checks for updates on launch and every hour and offers **Check updates** and **Update** actions ([Updates](#updates), [versioning.md](versioning.md)). This file is the reference for the app. Any change to the app follows it, and a change to the app's behavior or design updates it.

## Purpose and scope

- **Status.** The window shows the Hub state, the Hub mode (fast or activity UI), one row per child MCP, and, with the activity UI, the live AI clients plus those active in the last 30 minutes. With the activity UI, a child in use gets a short line saying which AI client is using it now, or used it in the last 2 minutes, and each AI client row shows the children it uses (see [Child usage line](#child-usage-line)).
- **Main actions.** **Start Hub** runs `hubctl start` in the background, without a window. **Open dashboard** pairs a browser session and opens `/dashboard/`, as `hubctl open` does. The ⟳ button beside the Child MCPs count polls at once.
- **Mini bar (⤡).** The ⤡ button in the Hub card shrinks the app to a one-line bar that stays above other windows: one dot per child (blue `◉` while an AI client calls it), the connected count, and the AI-client count; while the Hub is not running, the Hub's dot and state word instead. Drag it anywhere; ⤢ brings the full window back. The app reopens in the mode and at the place it was left (see [Design keywords](#design-keywords)).
- **Settings (⚙).** The ⚙ button in the Hub card opens the Settings card. It has four tabs:
  - **Appearance:** theme (Light/Dark), language (EN/VI), frosted glass on or off, card opacity (20–100 %), and background tint (0–90 %). These are remembered in the app's own `%APPDATA%\mcp-hub-status\desktop.json`. **Reset to defaults** turns glass on and restores the theme's tuned opacity and tint, keeping the theme and language. Without a saved choice, the app follows the Windows app theme and display language.
  - **Hub:**
    - Start, Restart, and Stop the Hub (`hubctl start`, `restart`, `stop`).
    - **Track AI clients**, the activity UI saved for every start (see [Hub settings](#hub-settings)).
    - **BitNet (local AI)**, plan requests and condense large results on this PC. While it is not installed the tab shows **Install BitNet**; see [BitNet install](#bitnet-install).
    - **Start Hub at sign-in** (the Hub's task, changed through the Hub's API) and **Open this app at sign-in** (the app's own entry in the user's Run key; only in the installed app).
  - **MCPs:** each child MCP on or off.
  - **Config:** each child MCP's required settings and the state of its requirements file (see [Child config files](#child-config-files)). The ⚙-style edit button opens the child's private config file in the user's editor; the app never reads its values.

  The Hub keeps these settings, so the Hub and MCPs tabs show them only while it runs (otherwise `desktop.settings.needsHub`). A change to the tracking mode or a child is saved by the Hub to `config/private/hub.json` and applies at the next Hub start; the card then shows **Restart Hub**.
- **Update card.** The card reports updates for the Hub and enabled child MCPs, and stays compact: the title with **What's new** beside it, one status sentence, one summary line (`done/total done`, then only the components that are not finished, with their state or error; the tooltip lists every component), and a rolling log of the newest progress line per component (three rows, newest on top, each older row fainter, fixed height). One main button shows at a time: **Update**, or **Retry** for failed components; a ⟳ button checks now. The status sentence is red only when installing the app itself failed. The app installer runs last when a newer release exists (see [Updates](#updates)).
- **Not in scope.** Jobs, write approvals, logs, tool allowlists, cache times, and every secret stay in the dashboard, `hubctl`, and the private files. That covers child credentials and `agents.json`. The app never shows tool arguments, results, tokens, or file paths.

## Install, run, disable

| Action | How |
|---|---|
| Get or refresh the app | Double-click `Setup.cmd` in the mcp-hub checkout, or run `.\scripts\hubctl.ps1 app -Install`. Both use the latest stable GitHub Release and verify the installer SHA-256. Setup installs the app if missing and refreshes it when the Release is newer; if it is open, Setup closes and reopens it while preserving its sign-in choice. An existing app is kept if the check, download, checksum, or installer fails. For a first install only, `hubctl app -Install` may fall back to a build in `dist/` (which may be older), then to running from source. A private repository's release needs a signed-in download. |
| Install the app | Run `mcp-hub-status-setup-<version>.exe` (per user, no admin). Choose a desktop icon and **Open at sign-in**. |
| Open the app | Start Menu or desktop → **mcp-hub Status**, or `.\scripts\hubctl.ps1 app` |
| Run it from source (development) | `uv run python -m mcp_hub.desktop` |
| Turn opening at sign-in off / on | Settings → Hub → **Open this app at sign-in** (installed app only) |
| Turn the Hub's start at sign-in off / on | Settings → Hub → **Start Hub at sign-in**, or `.\scripts\hubctl.ps1 autostart -Target hub -State off` / `on` |
| Update | Use **Check updates** or **Update** in the installed app. It updates enabled child MCPs, the Hub, and the app release when available. How versions and upgrades work: [versioning.md](versioning.md). |
| Run the downloaded installer again (same or older version) | It does not downgrade or reinstall the app; use `Setup.cmd` to refresh to a newer stable Release. |
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

`scripts/build_desktop.py` draws the icon with Qt, bundles the app with PyInstaller (`packaging/desktop/mcp-hub-status.spec`, one folder, no console), checks that the bundle carries the Microsoft Visual C++ runtime (`BUNDLED_RUNTIME`) and package metadata version, starts the bundled exe once in `--screenshot` mode as a self-check (a failure that happens only in the frozen app stops the build), and compiles `packaging/desktop/installer.iss` with Inno Setup. `--isolated` builds in a temporary environment, so it never syncs the shared `.venv`, whose entry-point exes the running Hub and AI-client bridges keep locked. The version is the one in `pyproject.toml`. Everything goes to `build/` and `dist/`, which git ignores; never commit them. The `build` dependency group holds PyInstaller and is synced only on request.

Before anything is installed, the setup:

- **Looks for an installed copy.** Setup uses the latest stable Release, installs it if missing, and updates an older app in place after verifying the SHA-256. The app's folder and preferences are preserved.
- **Shows a System check page.**
  - **Windows:** 10 version 1809 or later, 64-bit (`MinVersion`), which ships the Universal C runtime.
  - **Libraries:** Python, Qt and the Visual C++ runtime ship inside the app, so nothing else is installed.
  - **Disk space:** at least 300 MB free, or setup stops.
  - **The Hub:** whether its connection file exists. A missing file is only a warning; setup checks existence only and never reads the token.

The installer is not code-signed, so Windows SmartScreen may show "Unknown publisher" the first time (More info → Run anyway).

## Updates

How releases, numbering, and upgrades of the other parts work is in [versioning.md](versioning.md); this section is what the app does.

- **Check.** On a worker thread at app launch and every hour while open; the user can check manually. It checks the latest stable Release and asks the local Hub to check its checkout and enabled installed child repositories. An app run from source never offers installation.
- **Version.** The app reports the `mcp-hub` version it was built from (`importlib.metadata`; the spec bundles the metadata, and `build_desktop.py` checks the bundled metadata against `pyproject.toml`). `python -m mcp_hub.desktop --version` prints it. When more than one `mcp-hub` metadata folder is found, the highest version wins; the installer also deletes the previous `_internal` folder before copying, so an upgrade leaves no stale metadata.
- **Install.** The updater starts the Hub's scheduled task when needed. It stops the Hub, updates enabled child checkouts concurrently, updates the Hub, prepares dependencies, restarts and smoke-checks. Local edits, local-only commits, diverged branches, and branches without an upstream are skipped safely. The app installer runs last after SHA-256 verification, and only after a run the user started with **Update** or **Retry**; a startup, hourly, or manual check never installs the app or closes it.
- **Progress and retry.** A bounded sanitized status file under `%LOCALAPPDATA%\mcp-hub-status\update.json` lets the app show progress while the Hub restarts and after app restart. It contains component IDs, phases, timestamps, and error categories, never raw process output or credentials. Retry reruns failed components and dependent preparation/checks.
- **Trust.** The SHA-256 comes from the same Release, so it catches damage, not a changed Release; the installer is not code-signed (see [versioning.md](versioning.md#what-update-does)).

## Hub settings

## Child config files

A child MCP can declare the settings it needs in `hub-requirements.json` in its own repository (format in [child-mcp-compatibility.md](child-mcp-compatibility.md#declaring-required-settings)). The app shows the result in two places:

- **Child MCPs card.** A child that started with a gap shows the warning tone and **Config incomplete**. It still runs; its tools work from its own local files. Clicking that row opens Settings on the Config tab with the child's edit button focused.
- **Settings → Config.** One row per child with its state: **Ready**, **Missing: <key paths>** (names only), **Ready. Restart the Hub to apply.** (the file is complete but the running Hub started with the gap), **No requirements declared**, or **Requirements file is invalid**.

The edit button asks the Hub to create the child's private file from its template when it is missing (`POST /dashboard/api/settings/servers/{id}/config-file`), then opens that file in the default editor. The Hub returns only the path. The app never reads the file and never shows its path or values. With `MCP_HUB_CONFIG` set, or while the Hub is not running, the button is disabled and its tooltip says why. A press that fails shows the reason on that child's row.

The Hub and MCPs tabs call the Hub's settings API (`dashboard/settings_api.py`, see [Architecture](#architecture)). The Hub then changes `config/private/hub.json` through `src/mcp_hub/hub_settings.py`, the only writer of these values:

- **`servers.<id>.enabled`.** The MCPs tab lists the children found next to the Hub (`../<id>`) or listed in `hub.json`. It never offers a missing repository, which would stop the Hub from starting. A child whose private config still holds `<SET_BY_USER>` shows "needs credentials".
- **Top-level `extensions`.** `["activity-ui"]` keeps the activity UI on for every start, logon starts included, until it is turned off. The service starts the union of this list and the one-shot `launch.json` that `hubctl ... -Ui` writes. `load_config` ignores the key.

Writes are atomic and keep every other key as it is. Only booleans and the extension list are written, so `hub.json` stays free of secrets. With `MCP_HUB_CONFIG` set, the file belongs to the user: the API refuses changes (409) and both tabs show the values read-only.

Restart and Stop disconnect AI clients for a moment, so they need a second click within `CONFIRM_MS` (4 s); the armed button turns `red` and reads `desktop.settings.confirm`.

### BitNet install

The app never builds or downloads anything itself: it asks the Hub (`POST /dashboard/api/bitnet/install`) and shows the `bitnet` block of `/dashboard/api/status` (owned by [bitnet.md](bitnet.md#install-from-the-desktop-app)). The Hub tab's BitNet line is a tone icon, a caption, and at most one action:

| Hub reports | Glyph | Caption | Action |
|---|---|---|---|
| not installed | off icon | `desktop.bitnet.state.not_installed` | **Install BitNet** |
| install running | turning `◐◓◑◒` (`Spinner`, `SPINNER_MS`) | `desktop.bitnet.step.{prepare,runtime,model,extract}` with the percent | none |
| install failed | `●` `red` | `desktop.bitnet.error.<code>` | **Try again** |
| installed, BitNet saved on, not running in this Hub | `◐` | `desktop.bitnet.restartToConnect` | **Restart Hub** (no second click) |
| installed and running | the sidecar's tone | `desktop.bitnet.state.<state>` | none |

The On/Off switch shows only once BitNet is installed. While the install runs or BitNet starts, the app reads the Hub every `BUSY_POLL_INTERVAL_MS` (1 s) instead of every 5 s, so the percent and the dot move. A Hub older than this feature sends no install block; the line then shows only the sidecar state, as before.

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
| `desktop/processing.py` | Logic, no Qt. Validates the content-free processing SSE contract, opens the authenticated loopback stream, reconnects with bounded backoff, and forwards typed snapshots/events to the Qt queue. `ProcessingBoard` deduplicates by Hub stream ID and sequence and keeps three newest records in memory. |
| `desktop/usage.py` | Logic, no Qt. `stream_usage` (a worker thread) reads `GET /dashboard/api/activity/stream` with the bearer and forwards its snapshot and call events. It reconnects with a backoff from 1 to `STREAM_RETRY_MAX_SECONDS` (30 s). `UsageBoard` keeps the running calls and the last finished call per child; `usage()` gives a child's line and `client_usage()` the children each client uses. |
| `desktop/connection.py` | Reads the connection file: `load_connection` accepts only a `127.0.0.1` URL and a token of at least 32 characters, and raises `ConnectionMissing` (shown as **Not installed**) otherwise. The token is never in an error or `repr`. |
| `desktop/hub_api.py` | `HubClient`: the Hub's settings API (`read_settings`, `set_activity_ui`, `set_bitnet`, `install_bitnet`, `set_server_enabled`, `read_hub_autostart`, `set_hub_autostart`) and `control` (restart or stop). It holds the app's own `HubSettings` and `ServerSwitch` dataclasses. Failures are `OSError` or `ValueError`. |
| `desktop/system.py` | What the app does on Windows itself: `start_hub` (the Hub's scheduled task, no window) and its own Run-key entry (`app_autostart`, `set_app_autostart`, only when frozen). |
| `desktop/status.py` | Logic, no Qt. It reads the URL and admin token from the connection file. It calls `GET /health`, then `GET /dashboard/api/status` with the bearer, and builds a `HubSnapshot`. `open_dashboard` sends `POST /dashboard/pair` and opens the link. Requests go only to the `127.0.0.1` URL in the connection file, bypass any configured proxy, and time out after 3 s. |
| `desktop/i18n.py` | `Translator`: text from the `desktop.*` keys (plus the shared `connection.*` and `clients.empty` keys) in `dashboard/locales/<lang>.json`; `system_language` reads the Windows display language. |
| `desktop/theme.py` | Every design token: the `LIGHT` and `DARK` palettes, the `GLASS` values per theme, tone tokens, spacing, sizes, fonts, timings. `stylesheet(theme, glass)` builds the Qt stylesheet of the window and its frosted cards; `apply_fluent(look, accent)` themes the Fluent controls; `backdrop_tint(theme)` gives the acrylic tint. |
| `desktop/widgets.py` | `Card` (the frosted panel, styled by `QFrame#card`), `Row` (tone icon, name, detail), `Page` (a Settings tab, with segmented, switch, and slider rows; `switch_row`), `set_switch` (a switch's value and enabled state), `row_room` and `show_elided` (the width of a line and its ellipsis), `ConfirmButton` (two-step confirm), `button` (Fluent push button), `icon_button` (Fluent icon-only button), `label` (plain label with a role), `tone_pixmap` and `arc_pixmap` (the tone icons and the busy arc), `set_props` (dynamic properties), and `dot_icon`. |
| `desktop/mini.py` | `MiniBar`, the pinned one-line bar of compact mode: dots updated in place, drag to move (`startSystemMove`). |
| `desktop/settings.py` | `SettingsPanel`, the Settings card (title, tab row, one visible page, restart notice), and `AppearancePage`. Pages report choices through callbacks and show the current ones through `sync`; `StatusWindow` applies and saves them. |
| `desktop/hub_page.py` | `HubPage` (Hub control, Track AI clients, BitNet switch, the two sign-in switches) and `ChildrenPage` (one On/Off row per child). Both show `desktop.settings.needsHub` while the Hub is not running. |
| `desktop/config_page.py` | `ConfigPage`, the Config tab: one row per child with its requirements state and the edit button. `config_status` maps a child's settings to a tone and a locale key. |
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

The BitNet processing reader is a separate daemon thread, started whenever the running Hub reports BitNet enabled, including when Track AI clients is off. It reads `GET /dashboard/api/bitnet/processing/stream` using the admin bearer and the existing loopback-only opener. A snapshot (also used for resync) replaces the board; event messages append in sequence order. Unknown or malformed records are ignored. Reconnect waits grow from 1 to `STREAM_RETRY_MAX_SECONDS`; HTTP 401/404 quietly clears the log once per unavailable stretch and retries with the same bounded backoff, so a restarted or upgraded Hub can recover without a footer error.

The processing reader sends typed records through the existing queue. Only its current generation may update the board on the Qt thread. Disabling BitNet, removing its status row, stopping/restarting the Hub, and closing the app cancel the generation and clear the board. A new Hub stream ID resets the sequence. No processing history is written to disk, Activity UI, or the footer.

### Incremental rendering

`render()` splits the window into sections: texts, Hub row, children, usage, clients, client usage, actions, settings, icon. The children and clients sections rebuild rows only when the child states or the client list change; the usage and client usage sections only set text on the existing rows. Each section has a key built from the data it shows, and a section is redrawn only when its key changed. A poll that reports the same state therefore changes only the footer time. Never rebuild rows or reset the icon on every poll: that made the first version flicker.

The outer layout uses `QLayout.SetFixedSize`, so the window follows its content without a manual resize. A theme switch applies one new stylesheet, and the Fluent theme when it changes, then redraws each section once (tone icons, taskbar icon); it never rebuilds widgets.

### Glass

On Windows, `win32.style_window` styles the native window in three steps:
1. a dark or light title bar (`DWMWA_USE_IMMERSIVE_DARK_MODE`);
2. rounded corners (`DWMWA_WINDOW_CORNER_PREFERENCE`);
3. the acrylic accent (`SetWindowCompositionAttribute`, `ACCENT_ENABLE_ACRYLICBLURBEHIND`), tinted with the theme's `canvas` color at `GLASS[theme]["tint_alpha"]`.

The window has `WA_TranslucentBackground`, and with glass the stylesheet leaves the window background transparent. Cards are the theme's `surface` at `card_alpha`, with a light edge (`GLASS[theme]["border"]`). The blurred desktop shows through every card, and Qt draws text with correct alpha, so text stays crisp.

**Contrast.** Every caption must reach 4.5:1 on the card over the worst-case desktop, white or black, in both themes, so the defaults are tuned for it. The light `muted` color is `#3e454e` (it was `#6b7788`). The dark card default alpha is 0.82 (it was 0.45). Measured on the default card and tint, light `muted` gives 9.60:1 on white and 4.51:1 on black, dark `muted` gives 4.61:1 on white and 6.48:1 on black, and `ink` passes everywhere. The dashboard's `--muted` token in `dashboard/style.css` matches the light value.

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
- **Four status tones.** Every state maps to exactly one tone. Do not add a tone without updating this list, `status.TONES`, `theme.TONE_TOKENS`, and `widgets.TONE_ICONS` (or `tone_pixmap` for `off`).

  | Tone | Color token | Icon | Meaning |
  |---|---|---|---|
  | `ok` | `green` | Fluent success (`InfoBarIcon.SUCCESS`) | Working |
  | `wait` | `amber` | Fluent warning (`InfoBarIcon.WARNING`), or a turning arc while busy | In progress, or checking |
  | `off` | `muted` | Outlined ring, painted in the muted color | Turned off or not set up, not a failure |
  | `error` | `red` | Fluent error (`InfoBarIcon.ERROR`) | Needs attention |

- **Shape + color + text.** A tone always comes with its icon and a state word, so it stays readable without color vision; each tone has its own shape. Icons sit in a fixed-width column (`GLYPH_COLUMN_WIDTH`, 16 px), so names line up whatever the icon. Each status icon has an accessible name equal to its state word (`Row.show_state` sets it), so a screen reader names it.
- **Status icon.** The window and taskbar icon is a dot in the Hub's tone, so the state is visible while the window is in the background. It changes only when the tone changes.
- **Frosted glass.** A narrow window (`WINDOW_WIDTH` 320 px) holds four rounded cards (`CARD_RADIUS` 8): Hub, Child MCPs, AI clients, actions. Cards are translucent `surface` (`GLASS[theme]["card_alpha"]`) with a light 1 px edge, over the blur of whatever is behind the window (Frosted or Clear style). Text and icons are always fully opaque. The window fits its content and cannot be resized or maximized. The native title bar stays.
- **Cards carry all content.** Every label, button, and note sits inside a card; the space between cards is pure glass. Every card title uses the section role, and a list card's heading has a count on the right: `2/4` connected children, or the number of live clients.
- **Two themes, one token set.** `LIGHT` mirrors the `:root` tokens of `dashboard/style.css`, and a test fails if they drift apart. `DARK` uses the same token names, with dark surfaces and brighter tone colors. Each theme has its own `GLASS` defaults: the light theme uses a lighter tint and a white edge, the dark theme a deeper tint, a faint edge, and a card default of 0.82 alpha (see [Glass](#glass)). The user's opacity and tint (`theme.Appearance`) override them; `None` means the theme default, so switching themes keeps each theme's tuning until the user moves a slider. Colors reach widgets only through `theme.stylesheet` (the `role` and `tone` properties) and the Fluent theme (`theme.apply_fluent`); code never sets a color on a widget, except the painted tone icons, which take the palette of the current theme.
- **Spacing scale.** 4 / 8 / 12 / 16 px (`SPACE_XS` … `SPACE_L`), in Qt device-independent pixels (Qt scales them with the screen). Cards are separated by `SPACE_M` (12 px). No other spacing values.
- **Typography.** `Segoe UI Variable Text` (then `Segoe UI`) on Windows, the Qt default elsewhere, in four styles set by the stylesheet roles: headline 12 semibold (the Hub state), section heading 8 bold (every card title uses it), body 10, caption 9 in `muted`. Labels stay plain `QLabel`s: Fluent's label classes restyle on every role change, which made the window several times slower. Fluent's own type is used by its controls only.
- **Accent.** Primary buttons and switches use the Windows accent (the Qt palette's accent) when white text on it reads at 3:1 or better; otherwise `blue`, so a pale accent never makes a button label unreadable.
- **One primary action.** The main view shows exactly one primary button (Fluent `PrimaryPushButton`, in the accent): **Start Hub** while the Hub is stopped, **Open dashboard** while it runs. The other is hidden, not disabled. **Update** is a plain `PushButton` unless it is the only actionable item (the Hub is running and an update is offered); then it is the primary, and **Open dashboard** is plain. The Settings card's **Restart Hub** and the other buttons are plain Fluent `PushButton`s. Only the enabled primary acts. Apart from that pair, a button that cannot act is disabled, never hidden. Icon-only buttons (⚙, ⤡, ⤢, ⟳) are Fluent `TransparentToolButton`s, each with a tooltip in the current language.
- **Responsive controls.** Fluent controls have hover, pressed, and disabled states, and a pointing-hand cursor. A Fluent button is as wide as its label needs, so a row of buttons must leave room for the longest label in both languages (a test checks the update card).
- **Elided text.** A line that may be long is cut with "…" to the room its row or card has (`widgets.show_elided`). `Row.show_state` cuts the state word and the detail, using `row_room` for the width they share, and the tooltip holds the full text.
- **Settings card.** The card is inline, right below the Hub card, never a second window, and the window grows to fit it.
  - The title is on the first line and the tab row (Appearance | Hub | MCPs | Config) on the second, full width, because four tabs do not fit beside the title. One tab shows at a time, so the card stays short.
  - Each row is a caption on the left and a control on the right. Theme, language, glass, glass style, and the tabs are a Fluent `SegmentedWidget` (one segment per value; its route key is `str(value)`; the selected one has Fluent's indicator). The four on/off choices (Track AI clients, BitNet, Start Hub at sign-in, Open this app at sign-in) and each child MCP's on/off row are Fluent `SwitchButton`s made by `Page.switch_row`, with the On/Off text from `desktop.settings.on` and `off`; `widgets.set_switch` shows the value, and an unknown value (the Hub is not running) shows off and disabled. Amounts are Fluent sliders with their value in percent.
  - A control that cannot act is disabled: the sliders while glass is off, glass where it is unavailable (with a caption), values still loading (with `desktop.settings.loading`), and every Hub setting with `MCP_HUB_CONFIG` set.
  - Appearance changes apply live; a save follows `PREFS_SAVE_DELAY_MS` after the last change, or on close. Hub settings are saved at once and apply at the next start.
- **Confirm before disconnecting clients.** Restart and Stop need a second click within `CONFIRM_MS`; the armed button is `red`. Nothing else in the app interrupts AI clients.
- **Bilingual by data.** Every visible string, tooltip included, comes from a locale key. The only text glyph is the usage arrow (`↳`) in `theme.py`; every other symbol is an icon. The language codes (`EN`, `VI`) are text.
- **Never block, never leak.** Network work runs off the Qt thread. The app shows only state names and client names; never the token, private paths, or raw error text. A failed action shows one fixed sentence in the footer.
- **Mini bar.** Compact mode is one rounded card with no title bar, always on top, and dragged by its body.
  - While the Hub runs, left to right: one dot per child in its tone (a filled `blue` dot while an AI client calls it), the `ok/total` count, `desktop.mini.clients` with the activity UI, and the ⤢ button. There is no separate Hub dot, so the number of dots always matches the count.
  - While the Hub is not running, the bar shows the Hub's tone dot and its state word (e.g. the `wait` icon and `Starting…`) instead of child dots.
  - Names and details are in tooltips (`desktop.mini.dot`), never on the bar.
  - Dots are updated in place and rebuilt only when the children change.
  - Switching modes recreates the native window, so the style is applied again.
- **Child usage line.** A short, caption-size line under the child's name, `↳ {clients} · {when}`, indented past the icon column. It exists only while there is usage: an idle child is a single row, so the list keeps an even rhythm.
  - It is `blue` (`role="live"`) with `desktop.usage.now` while calls run, then `muted` with "just now" / "N min ago" for `RECENT_SECONDS` (2 min). Whole minutes keep the text from changing more than once a minute.
  - Several clients merge as `Codex ×2, Antigravity`.
  - The tool name is not on the line; the tooltip (`desktop.usage.detail`) carries it when all running calls use one tool.
  - The line stays 2 minutes after the last call, so the window grows once per burst of calls and shrinks once after it, not on every call.
- **Client usage detail.** An AI client row's right-hand detail names the children that client uses: `blue` while it calls them, `muted` for 2 minutes after, then back to its usual detail. One board feeds both cards, so they always agree.
- **Update card.** One card between the AI clients and the actions, shown only while an update is available and only in the full window (the mini bar never shows it). The card title is UPDATES (`desktop.update.title`; its Vietnamese value is in `dashboard/locales/vi.json`) in the section role. The version line below it is body text and names the version; a caption says which one is in use. **Update** is a plain button unless it is the only actionable item (the Hub is running and an update is offered); then it is the primary, and **Open dashboard** is plain (see One primary action above). A button that cannot act is disabled, never hidden: it waits while a download runs and while the health gate blocks (the caption then names the failing children). A failed update shows one fixed sentence in `red` and enables the button again. No version string, path, or error text from GitHub is shown.
- **Steady.** A refresh that finds nothing new must not move, resize, or redraw anything except the footer time (see [Incremental rendering](#incremental-rendering)). Only new usage or processing rows may change the height.

## States

| `hub` | Tone | Text key | What the user does |
|---|---|---|---|
| `running` | `ok` | `desktop.hub.running`; the mode caption (`desktop.mode.fast` or `desktop.mode.activityUi`) is a line of its own | Nothing. Open dashboard is the primary action. |
| `starting` | `wait` | `desktop.hub.starting` | Wait. Shown for up to `START_GRACE_SECONDS` (120 s) after Start Hub while `/health` does not answer yet. |
| `stopped` | `error` | `desktop.hub.stopped` | Press Start Hub, or run `hubctl status`. |
| `not_installed` | `off` | `desktop.hub.notInstalled` | There is no valid connection file: the Hub never started for this user. Run `hubctl install`. |
| `unauthorized` | `error` | `desktop.hub.unauthorized` | The Hub refused the token (HTTP 401): run `hubctl restart`. |

Before the first poll the Hub row shows `desktop.checking` in the `wait` tone. While the Hub runs, the mode (`desktop.mode.*`) is a `muted` caption on its own line under the state word; the Hub row shows only the state word.

| Child state (from `runtime.child_states()`) | Tone | Text key |
|---|---|---|
| `connected` | `ok` | `connection.connected` |
| `connecting`, `reconnecting` | `wait` | `connection.connecting`, `connection.reconnecting` |
| `disabled` | `off` | `connection.disabled` |
| `credentials required`, `unavailable`, any other reason | `error` | `connection.credentialsRequired`, `connection.unavailable`, or the reason as reported |
| `connected`, but started with a required setting empty (`config_incomplete`) | `wait` | `connection.configIncomplete`, in place of the state word; the whole row is a button that opens the Config tab for that child |

Under the children, one **BitNet** row has its own dot and is not counted in `ok/total`. It is green only when llama-server answers `/health`; a Hub too old to report BitNet shows no row.

| BitNet state (`status.bitnet.state`) | Tone | Detail |
|---|---|---|
| `ready` | `ok` | none |
| `installing` | `wait`, turning | the download percent, else `desktop.bitnet.state.installing` |
| `starting`, `restarting` | `wait` | `desktop.bitnet.state.<state>` |
| `unavailable` | `error` | `desktop.bitnet.state.unavailable` |
| `not_installed`, `off`, `stopped`, or installed but switched off | `off` | `desktop.bitnet.state.<state>` or `desktop.bitnet.state.off` |

### BitNet processing log

Directly beneath the BitNet status row, `ProcessingLog` shows at most three newest-first rows. Each line derives its operation, outcome, and optional source from locale keys, with optional configured server/tool IDs. It never displays prompts, arguments, results, model output, client labels, or exception text. Long lines elide and retain the safe localized line in a tooltip.

Native `QPropertyAnimation` moves retained rows downward within the panel over `SPINNER_MS`. Native `QGraphicsOpacityEffect` renders the newest row at full opacity and the next two at 65% and 40%. Row heights follow the current font, spacing follows the existing theme tokens, and each theme supplies its caption color. Empty history occupies no space. A burst redraws only this panel, preserving child usage rows, the BitNet ready dot, and mini-bar state. Language changes re-render the stored event codes; compact mode hides the panel with its containing card.

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
- `desktop.settings.{confirm,pending,restartNow,loading,needsHub}`, `desktop.settings.tab.{appearance,hub,mcps,config}`
- `desktop.settings.config.{ready,readyRestart,missing,none,invalid,open,opened,note}`, `desktop.settings.config.disabled.{needsHub,readOnly}`
- `desktop.error.configOpen.{hub,editor}`, `connection.configIncomplete`
- `desktop.settings.hub.{control,start,restart,stop,tracking,trackingHint,autostartHub,autostartApp,autostartAppSource}`
- `desktop.settings.mcps.{empty,readOnly,credentials}`
- `desktop.section.{children,clients}`
- `desktop.checking`
- `desktop.hub.{running,starting,stopped,notInstalled,unauthorized}`
- `desktop.mode.{fast,activityUi}`
- `desktop.waitingForHub`, `desktop.children.empty`, `desktop.clients.hint`
- `desktop.button.{openDashboard,startHub}`
- `desktop.updated`
- `desktop.update.{title,available,current,button,whatsNew,downloading,installing,failed,blocked,hubNotChecked}`
- `desktop.error.{dashboard,controlUnavailable,startTimeout,settings,autostart}`
- `desktop.settings.hub.{bitnet,bitnetHint}`
- `desktop.bitnet.{name,install,retry,restart,restartToConnect}`
- `desktop.bitnet.step.{prepare,runtime,model,extract}`
- `desktop.bitnet.error.{platform,disk,network,release,checksum,extract,cancelled,unexpected,request}`
- `desktop.bitnet.state.{not_installed,off,installing,starting,ready,restarting,unavailable,stopped}`
- `desktop.usage.{line,detail,now,justNow,minutesAgo}`
- `desktop.processing.{line,sourceLine}`
- `desktop.processing.operation.{plan,child_call,condense,job_plan,local_summary}`
- `desktop.processing.state.{started,succeeded,failed,fallback}`
- `desktop.processing.source.{bitnet,rules,child,cache,fallback,local}`

It also uses the dashboard's `connection.*`, `clients.empty`, and `clients.recent`.

Rules:
- Add a key to `en.json` and `vi.json` in the same change, and remove keys the app no longer uses.
- The language segment shows codes (`EN`, `VI`) rather than language names, so `en.json` never needs Vietnamese text.
- Text in code, tests, and this doc stays English. The only Vietnamese in the repository is the values in `vi.json`.

## Screenshot review

```powershell
uv run python -m mcp_hub.desktop --screenshot <folder> [--states running,ui,starting,stopped,not_installed,unauthorized,update,update-blocked,settings,settings-hub,settings-mcps,compact] [--lang en,vi] [--theme light,dark]
```

The command opens the window on top of other windows and renders fixed sample data for each listed state, language, and theme. The `settings`, `settings-hub`, and `settings-mcps` states are the running sample with that Settings tab open, filled with sample Hub settings. The `update` and `update-blocked` states are the running sample with the update card, without and with a failing child. The update sample offers `screenshot.SAMPLE_RELEASE` (0.5.1) over `SAMPLE_CURRENT` (0.5.0), so it is a real newer version. The `compact` state is the `ui` sample as the mini bar. It saves `<state>-<language>-<theme>.png` for each combination, prints the paths, and exits.

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

- rows and counts, tone properties, and the primary buttons;
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

`tests/test_desktop_processing.py` covers typed snapshot/event/resync parsing, malformed and unknown records, content-bearing field projection, stream/sequence deduplication, restart, stop, authenticated loopback opening, bounded reconnect, and quiet older-Hub behavior. Window tests cover placement, newest-three order and opacity, locale changes, compact mode, unchanged child rows, tracking independence, generation cancellation, disable/restart/close, and clearing a missing route without a footer error.

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

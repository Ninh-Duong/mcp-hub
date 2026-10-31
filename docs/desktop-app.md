# Desktop status app

A small frosted-glass window (Qt) that opens at every Windows logon. It shows whether the shared Hub is running, whether each child MCP is connected, and, when the Hub tracks AI clients (the activity UI), which AI clients use it. Its Settings card holds the Hub's everyday options. This file is the reference for the app. Any change to the app follows it, and a change to the app's behavior or design updates it.

## Purpose and scope

- **Status.** The window shows the Hub state, the Hub mode (fast or activity UI), one row per child MCP, and, with the activity UI, the live AI clients plus those active in the last 30 minutes. With the activity UI, a child in use gets a short line saying which AI client is using it now, or used it in the last 2 minutes, and each AI client row shows the children it uses (see [Child usage line](#child-usage-line)).
- **Main actions.** **Start Hub** runs `hubctl start` in the background, without a window. **Open dashboard** pairs a browser session and opens `/dashboard/`, as `hubctl open` does. The ⟳ button polls at once.
- **Mini bar (⤡).** The ⤡ button in the Hub card shrinks the app to a one-line bar that stays above other windows: one dot per child (blue `◉` while an AI client calls it), the connected count, and the AI-client count; while the Hub is not running, the Hub's dot and state word instead. Drag it anywhere; ⤢ brings the full window back. The app reopens in the mode and at the place it was left (see [Mini bar](#mini-bar)).
- **Settings (⚙).** The ⚙ button in the Hub card opens the Settings card. It has three tabs:
  - **Appearance:** theme (Light/Dark), language (EN/VI), frosted glass on or off, card opacity (20–100 %), and background tint (0–90 %). These are remembered in `config/private/desktop.json`. **Reset to defaults** turns glass on and restores the theme's tuned opacity and tint, keeping the theme and language. Without a saved choice, the app follows the Windows app theme and display language.
  - **Hub:**
    - Start, Restart, and Stop the Hub (`hubctl start|restart|stop`).
    - **Track AI clients**, the activity UI saved for every start (see [Hub settings](#hub-settings)).
    - **Start Hub at sign-in** and **Open this app at sign-in** (`hubctl autostart`).
  - **MCPs:** each child MCP on or off.

  A change to the tracking mode or a child is saved to `config/private/hub.json` and applies at the next Hub start. While the Hub runs, the card then shows **Restart Hub**.
- **Not in scope.** Jobs, write approvals, logs, tool allowlists, cache times, and every secret stay in the dashboard, `hubctl`, and the private files. That covers child credentials and `agents.json`. The app never shows tool arguments, results, tokens, or file paths.

## Install, run, disable

| Action | Command |
|---|---|
| Register the logon task and open the app | `.\scripts\hubctl.ps1 install` |
| Open the app now | `.\scripts\hubctl.ps1 app` |
| Run it without the task | `uv run python -m mcp_hub.desktop` |
| Turn autostart off / on again | Settings → Hub → **Open this app at sign-in**, or `.\scripts\hubctl.ps1 autostart -Target app -State off` / `on` |
| Show both sign-in choices | `.\scripts\hubctl.ps1 autostart` (prints `{"hub":true,"app":true}`) |
| Remove the logon task | `Unregister-ScheduledTask mcp-hub-status` |
| Save review screenshots of every state | `uv run python -m mcp_hub.desktop --screenshot <folder>` (see [Screenshot review](#screenshot-review)) |

Autostart off disables only the task's logon trigger. The task itself stays enabled, so `hubctl app`, `hubctl start`, and the app's Start button keep working. `hubctl install` registers both tasks again and keeps an autostart that was turned off.

- The scheduled task `mcp-hub-status` runs `.venv\Scripts\pythonw.exe -m mcp_hub.desktop` at logon, with limited privileges and no console. It uses the venv's `pythonw.exe`, not an entry-point exe, because `uv sync` cannot replace an exe that is running.
- Only one copy runs. A second start finds the `Local\mcp-hub-status` mutex taken and exits, so `hubctl app` never opens a second window.
- Closing the window exits the app. It comes back at the next logon or with `hubctl app`.
- The window has a fixed size that fits its content; it cannot be resized or maximized.
- The running app keeps Qt libraries in `.venv` open, so `hubctl` stops it before an environment sync (`Stop-StatusApp`, only this checkout's copy) and reopens it once the Hub is healthy (`Restore-StatusApp`).
- At logon the app can open before the Hub has started. It shows **Not running**, then **Running** at the next poll.

## Hub settings

The Hub and MCPs tabs change `config/private/hub.json` through `src/mcp_hub/hub_settings.py`, the only writer of these values:

- **`servers.<id>.enabled`.** The MCPs tab lists the children found next to the Hub (`../<id>`) or listed in `hub.json`. It never offers a missing repository, which would stop the Hub from starting. A child whose private config still holds `<SET_BY_USER>` shows "needs credentials".
- **Top-level `extensions`.** `["activity-ui"]` keeps the activity UI on for every start, logon starts included, until it is turned off. The service starts the union of this list and the one-shot `launch.json` that `hubctl ... -Ui` writes. `load_config` ignores the key.

Writes are atomic and keep every other key as it is. Only booleans and the extension list are written, so `hub.json` stays free of secrets. With `MCP_HUB_CONFIG` set, the file belongs to the user: both tabs show the values read-only.

Restart and Stop disconnect AI clients for a moment, so they need a second click within `CONFIRM_MS` (4 s); the armed button turns `red` and reads `desktop.settings.confirm`. A change made while the Hub is stopped needs no restart: the next start reads it.

The sign-in switches run `hubctl autostart` (`control.autostart_state` / `control.set_autostart`) on a worker thread. `hubctl` owns the scheduled tasks, and the app never edits them itself.

## Architecture

The app is a client of the shared HTTP service, like the stdio bridge. Core modules never import it, and it imports neither `server`, `http`, nor an extension. The view uses Qt (`PySide6-Essentials`: QtCore, QtGui, QtWidgets only).

| Module | Role |
|---|---|
| `desktop/usage.py` | Logic, no Qt. `stream_usage` (a worker thread) reads `GET /dashboard/api/activity/stream` with the bearer and forwards its snapshot and call events. It reconnects with a backoff from 1 to `STREAM_RETRY_MAX_SECONDS` (30 s). `UsageBoard` keeps the running calls and the last finished call per child; `usage()` gives a child's line and `client_usage()` the children each client uses. |
| `desktop/status.py` | Logic, no Qt. It reads the bearer token in process (`service_config.load_service_config(create=False)`). It calls `GET /health`, then `GET /dashboard/api/status` with the bearer, and builds a `HubSnapshot`. `open_dashboard` sends `POST /dashboard/pair` and opens the link. Requests go only to `127.0.0.1:8765`, bypass any configured proxy, and time out after 3 s. |
| `desktop/i18n.py` | `Translator`: text from the `desktop.*` keys (plus the shared `connection.*` and `clients.empty` keys) in `dashboard/locales/<lang>.json`; `system_language` reads the Windows display language. |
| `desktop/theme.py` | Every design token: the `LIGHT` and `DARK` palettes, the `GLASS` values per theme, tone tokens and glyphs, spacing, sizes, fonts, timings. `stylesheet(theme, glass)` builds the one Qt stylesheet; `backdrop_tint(theme)` gives the acrylic tint. |
| `desktop/widgets.py` | `Card` (the frosted panel, styled by `QFrame#card`), `Row` (tone glyph, name, detail), `Page` (a Settings tab, with segment and slider rows), `ConfirmButton` (two-step confirm), `button`, `label`, `set_props` (stylesheet properties), and `dot_icon`. |
| `desktop/mini.py` | `MiniBar`, the pinned one-line bar of compact mode: dots updated in place, drag to move (`startSystemMove`). |
| `desktop/settings.py` | `SettingsPanel`, the Settings card (title, tab row, one visible page, restart notice), and `AppearancePage`. Pages report choices through callbacks and show the current ones through `sync`; `StatusWindow` applies and saves them. |
| `desktop/hub_page.py` | `HubPage` (Hub control, Track AI clients, sign-in tasks) and `ChildrenPage` (one On/Off row per child). |
| `desktop/win32.py` | Windows integration, each with a harmless fallback: the window style (title bar theme, rounded corners, acrylic accent), the single-instance mutex, the Windows app theme, and screen capture. |
| `desktop/prefs.py` | Load and save the remembered choices in `config/private/desktop.json`: `theme`, `language`, `glass` (bool), `glass_style` (`frosted` or `clear`), `card_opacity` (0–100), `tint` (0–90), `compact` (bool), and `compact_pos` (two ints). One rule per key; unknown keys and invalid or out-of-range values are dropped. |
| `desktop/screenshot.py` | The `--screenshot` review mode: sample snapshots and capture (the composed screen on Windows, `QWidget.grab` elsewhere). |
| `desktop/app.py` | `StatusWindow(QWidget)`, the view only: layout, incremental rendering, button handlers. Also the command line, `application()`, and `main`. |
| `control.py` | Runs `hubctl.ps1` hidden through Windows PowerShell by absolute path: `launch_hubctl` (detached; shared with the dashboard's stop/restart buttons) and `run_hubctl` (waits for the output), used by `autostart_state` and `set_autostart`. |
| `hub_settings.py` | Reads and writes the Hub settings in `hub.json` (see [Hub settings](#hub-settings)). It sits at the `config` level, so the app uses it without reaching the running Hub. |

Data flow: every `POLL_INTERVAL_MS` (5 s), a `QTimer` starts a daemon thread that runs `fetch_snapshot`. That function never raises: each failure maps to a Hub state. The thread puts the result on a queue. A second `QTimer` drains the queue on the Qt thread every `QUEUE_CHECK_MS` (200 ms) and renders. Open dashboard, reading and writing `hub.json`, and `hubctl autostart` also run on daemon threads (`StatusWindow.run_worker`) and report back through the same queue. Start, Restart, and Stop only spawn a process, so they run on the Qt thread.

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

- **Shape + color + text.** A tone always comes with its glyph and a state word, so it stays readable without color vision. Glyphs sit in a fixed-width column (`GLYPH_COLUMN_CHARS`), so names line up whatever the glyph.
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
- **Steady.** A refresh that finds nothing new must not move, resize, or redraw anything except the footer time (see [Incremental rendering](#incremental-rendering)). Only new usage may change the height.

## States

| `hub` | Tone | Text key | What the user does |
|---|---|---|---|
| `running` | `ok` | `desktop.hub.running` (+ `desktop.mode.fast` or `desktop.mode.activityUi`) | Nothing. Open dashboard is the primary action. |
| `starting` | `wait` | `desktop.hub.starting` | Wait. Shown for up to `START_GRACE_SECONDS` (120 s) after Start Hub while `/health` does not answer yet. |
| `stopped` | `error` | `desktop.hub.stopped` | Press Start Hub, or run `hubctl status`. |
| `not_installed` | `off` | `desktop.hub.notInstalled` | `config/private/service.json` is missing or invalid: run `hubctl install`. |
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
- `desktop.settings.{confirm,pending,restartNow,loading}`, `desktop.settings.tab.{appearance,hub,mcps}`
- `desktop.settings.hub.{control,start,restart,stop,tracking,trackingHint,autostartHub,autostartApp}`
- `desktop.settings.mcps.{empty,readOnly,credentials}`
- `desktop.section.{children,clients}`
- `desktop.checking`
- `desktop.hub.{running,starting,stopped,notInstalled,unauthorized}`
- `desktop.mode.{fast,activityUi}`
- `desktop.waitingForHub`, `desktop.children.empty`, `desktop.clients.hint`
- `desktop.button.{openDashboard,startHub}`
- `desktop.updated`
- `desktop.error.{dashboard,controlUnavailable,settings,autostart}`
- `desktop.usage.{line,detail,now,justNow,minutesAgo}`

It also uses the dashboard's `connection.*`, `clients.empty`, and `clients.recent`.

Rules:
- Add a key to `en.json` and `vi.json` in the same change, and remove keys the app no longer uses.
- The language segment shows codes (`EN`, `VI`) rather than language names, so `en.json` never needs Vietnamese text.
- Text in code, tests, and this doc stays English. The only Vietnamese in the repository is the values in `vi.json`.

## Screenshot review

```powershell
uv run python -m mcp_hub.desktop --screenshot <folder> [--states running,ui,starting,stopped,not_installed,unauthorized,settings,settings-hub,settings-mcps,compact] [--lang en,vi] [--theme light,dark]
```

The command opens the window on top of other windows and renders fixed sample data for each listed state, language, and theme. The `settings`, `settings-hub`, and `settings-mcps` states are the running sample with that Settings tab open, filled with sample Hub settings. The `compact` state is the `ui` sample as the mini bar. It saves `<state>-<language>-<theme>.png` for each combination, prints the paths, and exits.

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
- **`control`:** the `launch_hubctl` and `run_hubctl` arguments, failures and timeouts as `OSError`, and the autostart JSON.

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
  - the tabs, the Hub tab (tracking, the confirmed restart and stop, sign-in switches), the MCPs tab, the read-only state, and errors in the footer;
- a theme switch that restyles without rebuilding;
- the glass stylesheet and the icon;
- polling on a worker thread with bounded waits, and that an unchanged poll redraws nothing;
- screenshot mode, the command line, and `main`.

The tests skip with a reason only if Qt cannot load; the CI Linux job installs the system libraries Qt needs.

`tests/test_desktop_usage.py` covers the SSE parser, the usage board (running and recent calls, merging, expiry, minute steps, snapshots, bad events), opening the stream with the bearer without showing it, and the worker's forwarding, backoff, and stop. `tests/test_desktop_window.py` also covers the usage lines and the client details: live updates without rebuilding rows, a hidden line giving its space back, the stream following the mode, stale messages dropped, and closing.

`tests/test_hub_settings.py` covers the `hub.json` writes: kept keys, a missing file, refused children, the saved mode, credentials, the read-only explicit config, and failed writes.

`tests/test_bootstrap_scripts.py` covers:
- the `mcp-hub-status` task and the `hubctl app` command;
- `hubctl autostart`, which switches only the logon trigger and is kept by `hubctl install`;
- that an environment sync stops this checkout's app first and reopens it afterwards.

Before you finish a change to the app:

1. Run `uv run python scripts/package_qc.py`. It covers English-only text, functions of at most 50 lines, and a test for every module.
2. Make sure the code uses no color, size, or spacing literal outside `theme.py`, and no visible string outside the locales.
3. Run the screenshot review for every state, both languages, and both themes. Check each image for alignment, clipping, contrast, and exactly one primary button. Put a colorful window behind the capture spot to judge the glass.
4. Run the real app (`uv run python -m mcp_hub.desktop`) for a minute against the running Hub. Check that it stays still between polls, and that it stays frosted while another window has focus.
5. Update this doc when you add a state, key, tone, token, or action.

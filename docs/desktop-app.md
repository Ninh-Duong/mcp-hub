# Desktop status app

A small window that opens at every Windows logon. It shows whether the shared Hub is running, whether each child MCP is connected, and, when the Hub runs with the activity UI, which AI clients use it. This file is the reference for the app. Any change to the app follows it, and a change to the app's behavior or design updates it.

## Purpose and scope

- **Read-only status.** The window shows the Hub state, the Hub mode (fast or activity UI), one row per child MCP, and the live AI clients (`-Ui` only).
- **Two actions.** **Start Hub** runs `hubctl start` in the background, without a window. **Open dashboard** pairs a browser session and opens `/dashboard/`, as `hubctl open` does. The ⟳ button polls at once.
- **Two view options.** A light/dark theme button (☾ / ☀) and an EN/VI language button in the Hub card. Both choices are remembered in `config/private/desktop.json`. Without a saved choice, the app follows the Windows app theme and display language.
- **Not in scope.** Jobs, write approvals, logs, and configuration stay in the dashboard and `hubctl`. The app never shows tool arguments, results, tokens, or file paths.

## Install, run, disable

| Action | Command |
|---|---|
| Register the logon task and open the app | `.\scripts\hubctl.ps1 install` |
| Open the app now | `.\scripts\hubctl.ps1 app` |
| Run it without the task | `uv run python -m mcp_hub.desktop` |
| Turn autostart off / on again | `Disable-ScheduledTask mcp-hub-status` / `Enable-ScheduledTask mcp-hub-status` |
| Remove the logon task | `Unregister-ScheduledTask mcp-hub-status` |
| Save review screenshots of every state | `uv run python -m mcp_hub.desktop --screenshot <folder>` (see [Screenshot review](#screenshot-review)) |

While the task is disabled, `hubctl app` fails too; run the app without the task instead. `hubctl install` registers the task again, enabled.

- The scheduled task `mcp-hub-status` runs `.venv\Scripts\pythonw.exe -m mcp_hub.desktop` at logon, with limited privileges and no console. It uses the venv's `pythonw.exe`, not an entry-point exe, because `uv sync` cannot replace an exe that is running.
- Only one copy runs. A second start finds the `Local\mcp-hub-status` mutex taken and exits, so `hubctl app` never opens a second window.
- Closing the window exits the app. It comes back at the next logon or with `hubctl app`.
- At logon the app can open before the Hub has started. It shows **Not running**, then **Running** at the next poll.

## Architecture

The app is a client of the shared HTTP service, like the stdio bridge. Core modules never import it, and it imports neither `server`, `http`, nor an extension.

| Module | Role |
|---|---|
| `desktop/status.py` | Logic, no Tk. It reads the bearer token in process (`service_config.load_service_config(create=False)`). It calls `GET /health`, then `GET /dashboard/api/status` with the bearer, and builds a `HubSnapshot`. `open_dashboard` sends `POST /dashboard/pair` and opens the link. Requests go only to `127.0.0.1:8765`, bypass any configured proxy, and time out after 3 s. |
| `desktop/i18n.py` | `Translator`: text from the `desktop.*` keys (plus the shared `connection.*` and `clients.empty` keys) in `dashboard/locales/<lang>.json`; `system_language` reads the Windows display language. |
| `desktop/theme.py` | Every design token: the `LIGHT` and `DARK` palettes, tone tokens and glyphs, the glass key, spacing, sizes, fonts, button styles, timings. |
| `desktop/widgets.py` | `Card` (a fixed-width rounded panel drawn on a canvas) and `dot_icon`. |
| `desktop/win32.py` | Windows integration, each with a harmless fallback: DWM window style (title bar theme, rounded corners, acrylic backdrop), the single-instance mutex, DPI awareness, the Windows app theme, and screen capture. |
| `desktop/prefs.py` | Load and save the remembered theme and language (`config/private/desktop.json`, known keys and values only). |
| `desktop/screenshot.py` | The `--screenshot` review mode: sample snapshots, capture, and a stdlib PNG encoder. |
| `desktop/app.py` | `StatusWindow`, the view only: layout, incremental rendering, button handlers. Also the command line and `main`. |
| `control.py` | `launch_hubctl`, shared with the dashboard's stop/restart buttons: runs `hubctl.ps1` hidden through Windows PowerShell by absolute path. |

Data flow: every `POLL_INTERVAL_MS` (5 s) a daemon thread runs `fetch_snapshot`. That function never raises: each failure maps to a Hub state. The thread puts the result on a queue, and the Tk thread drains the queue every `QUEUE_CHECK_MS` (200 ms) and renders. Open dashboard also runs on a daemon thread. Start Hub only spawns a process, so it runs on the Tk thread.

### Incremental rendering

`render()` splits the window into sections: texts, Hub row, children, clients, actions, icon. Each section has a key built from the data it shows. A section is redrawn only when its key changed. The window is resized (`Card.layout()`, then `geometry("")`) only when a section was redrawn. A poll that reports the same state therefore changes only the footer time. Never rebuild rows or reset the icon on every poll: that made the window flicker.

`Card.layout()` is called explicitly after a redraw. Tk sends no `<Configure>` event for a window item outside a canvas's visible area, so a card cannot grow from an event binding.

### Glass

On Windows 11 (22H2+), `win32.style_window` asks DWM for three things: rounded corners, a title bar in the current theme, and the acrylic backdrop (`DWMSBT_TRANSIENTWINDOW`). When the backdrop is on, the app paints the gaps between cards in `GLASS_KEY` (`#000000`) and sets that color as the window's `-transparentcolor`. Those pixels then show the blurred desktop behind the window.

- Cards stay opaque, so text keeps full contrast.
- Text is never placed on glass, because anti-aliased text drawn on the key color would fringe.
- Where the backdrop is unavailable (older Windows, other systems), the gaps use the palette's `canvas` color instead.
- Clicks on the glass gaps pass through to the window behind.

## Design keywords

These are the rules for every UI change in the app. A change that breaks one updates this section first.

- **Calm, glanceable.** The state is readable in one second. Color signals state only and is never decoration.
- **Four status tones.** Every state maps to exactly one tone. Do not add a tone without updating this list, `status.TONES`, `theme.TONE_TOKENS`, and `theme.TONE_GLYPHS`.

  | Tone | Color token | Glyph | Meaning |
  |---|---|---|---|
  | `ok` | `green` | ● | Working |
  | `wait` | `amber` | ◐ | In progress, or checking |
  | `off` | `muted` | ○ | Turned off or not set up, not a failure |
  | `error` | `red` | ● | Needs attention |

- **Shape + color + text.** A tone always comes with its glyph and a state word, so it stays readable without color vision. Glyphs sit in a fixed-width column (`GLYPH_COLUMN_CHARS`), so names line up whatever the glyph.
- **Status icon.** The window and taskbar icon is a dot in the Hub's tone, so the state is visible while the window is in the background. It changes only when the tone changes.
- **Compact glass.** A narrow window (`WINDOW_WIDTH`, 320 px at 96 DPI) holds four rounded cards (`CARD_RADIUS` 12) on a blurred, see-through backdrop: Hub, Child MCPs, AI clients, actions. The window fits its content and cannot be resized. The native title bar stays, for moving the window, the taskbar, and Alt+Tab.
- **Cards carry all text.** Every label, button, and note sits inside a card on `surface`, outlined in `line`. Nothing is drawn on the glass. A list card has a small uppercase `muted` heading, with a count on the right: `2/4` connected children, or the number of live clients.
- **Two themes, one token set.** `LIGHT` mirrors the `:root` tokens of `dashboard/style.css`, and a test fails if they drift apart. `DARK` uses the same token names, with dark surfaces and brighter tone colors. Widgets read colors only through the current palette, and switching themes rebuilds the widgets. Neither palette may use `GLASS_KEY`.
- **Spacing scale.** 4 / 8 / 12 / 16 px (`SPACE_XS` … `SPACE_L`), scaled with the screen DPI (`StatusWindow.px`). No other spacing values.
- **Typography.** The system font (`Segoe UI` on Windows, `TkDefaultFont` elsewhere), in four styles: headline 11 bold (the Hub state), section heading 8 bold, body 10, caption 9 in `muted`.
- **One primary action.** Exactly one `blue` button: **Start Hub** while the Hub is not running, **Open dashboard** while it runs. The other buttons are secondary (`surface` with `ink` text). A button that cannot act is disabled, never hidden. Small controls (theme, language, refresh) are glyph or code buttons.
- **Bilingual by data.** Every visible string comes from a locale key. The only literal glyphs are the tone, theme, and refresh glyphs in `theme.py`.
- **Never block, never leak.** Network work runs off the Tk thread. The app shows only state names and client names; never the token, private paths, or raw error text. A failed action shows one fixed sentence in the footer.
- **Steady.** A refresh that finds nothing new must not move, resize, or redraw anything except the footer time (see [Incremental rendering](#incremental-rendering)).

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

AI clients: only live clients are listed, one row each in the `ok` tone, with `×N` for several connections of one client. In fast mode the card shows `desktop.clients.hint`. With the activity UI and no clients it shows `clients.empty`. While the Hub is not running, both list cards show `desktop.waitingForHub`.

## Locale keys

The app's own keys:
- `desktop.language.switch`
- `desktop.section.{children,clients}`
- `desktop.checking`
- `desktop.hub.{running,starting,stopped,notInstalled,unauthorized}`
- `desktop.mode.{fast,activityUi}`
- `desktop.waitingForHub`, `desktop.children.empty`, `desktop.clients.hint`
- `desktop.button.{openDashboard,startHub}`
- `desktop.updated`
- `desktop.error.{dashboard,startUnavailable}`

It also uses the dashboard's `connection.*` and `clients.empty`.

Rules:
- Add a key to `en.json` and `vi.json` in the same change, and remove keys the app no longer uses.
- `desktop.language.switch` holds the code of the *other* language: `VI` in English, `EN` in Vietnamese.
- Text in code, tests, and this doc stays English. The only Vietnamese in the repository is the values in `vi.json`.

## Screenshot review

```powershell
uv run python -m mcp_hub.desktop --screenshot <folder> [--states running,ui,starting,stopped,not_installed,unauthorized] [--lang en,vi] [--theme light,dark]
```

The command opens the window on top of other windows and renders fixed sample data for each listed state, language, and theme. It saves `<state>-<language>-<theme>.png` for each combination, prints the paths, and exits.

- It never reads or changes the live Hub, so a review never restarts anything.
- Every list defaults to all values.
- The capture reads the screen as DWM composes it, so the images include the real backdrop blur. This works on Windows only.
- Keep the screenshot folder outside the repository, or in an ignored folder.
- The single-instance rule does not apply to this mode.

## Testing and review checklist

`tests/test_desktop.py` runs offline, with a mocked opener, browser, launcher, DWM, and `kernel32`. It covers:

- **Snapshot mapping:** every Hub state, the child tones, client merging, and malformed status fields.
- **Dashboard pairing:** a failure raises without opening a browser or showing the token.
- **Palettes:** `theme.LIGHT` equals the `style.css` `:root` tokens; both palettes share token names and avoid `GLASS_KEY`.
- **Language:** the default choice, placeholders, and that every key the app uses exists in both locales.
- **Remembered choices:** the round trip, unknown values dropped, broken or unwritable files ignored.
- **Windows integration:** the mutex, the DWM attribute calls and their fallback, the app theme, and a real screen capture (Windows only).
- **Screenshot mode:** PNG encoding, every combination saved, argument validation, and a failed capture.
- **The window:**
  - rows and counts, colors, and the primary button;
  - starting/stopped transitions and footer errors;
  - both toggles, with their saved choice;
  - the glass key and the icon;
  - polling on a worker thread with bounded waits;
  - an unchanged poll redraws nothing.

  These tests skip when Tk has no display (for example CI on Linux).

`tests/test_bootstrap_scripts.py` checks the `mcp-hub-status` task and the `hubctl app` command.

Before you finish a change to the app:

1. Run `uv run python scripts/package_qc.py`. It covers English-only text, functions of at most 50 lines, and a test for every module.
2. Make sure the code uses no color, size, or spacing literal outside `theme.py`, and no visible string outside the locales.
3. Run the screenshot review for every state, both languages, and both themes. Check each image for alignment, clipping, contrast, and exactly one primary button.
4. Run the real app (`uv run python -m mcp_hub.desktop`) for a minute against the running Hub, and check that it stays still between polls.
5. Update this doc when you add a state, key, tone, token, or action.

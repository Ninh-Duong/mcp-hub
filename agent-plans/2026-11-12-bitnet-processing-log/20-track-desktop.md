# Track 2: Desktop SSE client and rolling log UI

Status: done

Read `00-overview.md` and follow its event contract. Own only the files listed below. Do not edit core/BitNet backend files or plan files. Use TDD: write each test first, run it and confirm the expected failure, then implement the minimal change.

## Owns

- `src/mcp_hub/desktop/processing.py` (new)
- `src/mcp_hub/desktop/app.py`
- `src/mcp_hub/desktop/widgets.py` only if a focused log widget belongs there
- `src/mcp_hub/dashboard/locales/en.json`
- `src/mcp_hub/dashboard/locales/vi.json`
- `tests/test_desktop_processing.py` (new)
- focused assertions in `tests/test_desktop_window.py`
- `docs/desktop-app.md`

## Contract to implement

- Worker sends the admin bearer to the fixed loopback endpoint and parses SSE snapshot, event, keepalive, and resync messages. Validate event codes/field types and ignore malformed or unknown records.
- Use bounded reconnect backoff and a stop/generation token. Feed messages through the existing app result queue; widget mutation remains on the Qt thread.
- Start the processing reader whenever the current Hub reports BitNet enabled, regardless of Activity UI. Stop and clear when BitNet turns off, Hub stops/restarts, the row disappears, or the app closes. Old Hub 404/401 behavior is quiet and must not spam the footer.
- Deduplicate by stream ID and sequence. Snapshot repopulates the current Hub's last three events after reconnect; events from an obsolete generation are dropped.
- Put the compact panel directly under the BitNet row in the child-status card. Keep at most three newest-first rows; animate their downward movement and use native Qt opacity to mute older rows. Event codes map to English/Vietnamese strings; changing locale re-renders stored event codes.
- Do not rebuild child usage rows or alter mini-bar state on each event. Preserve fixed-window layout, both themes, and the existing BitNet ready dot.

## Tasks

- [x] T004 Add failing tests for SSE parsing, snapshot/resync, malformed data, reconnect/stop, event dedupe, and stale generations; implement the desktop worker/client.
- [x] T005 Add failing UI tests for three-row cap/order/fade, locale refresh, placement, Track AI clients off, stop/restart/disable/close, old Hub, unchanged child rows, and compact mode; implement the log widget and app wiring.
- [x] T006 Add EN/VI locale keys and update desktop module/data-flow/rendering documentation.

## Acceptance

- The UI shows no more than three rows, newest at top, and updates without touching widgets off the Qt thread.
- Processing stream lifecycle is independent from the Activity UI lifecycle.
- BitNet ready/off/installing status and Activity UI child/client rows remain unchanged.

## Progress log

- 2026-11-12 Codex: Contract frozen in overview; desktop track begins after baseline QC.

- 2026-11-12 Codex desktop agent: Completed typed processing SSE worker/board, independent Qt lifecycle, native animated three-row panel, EN/VI text, and desktop docs. Red-green tests pass (139 desktop tests); focused Ruff, docs, and code standards pass. Reviewed readable EN/VI light/dark offscreen renders; integrated review and final package QC/smoke remain with the coordinating agent.

- 2026-11-12 Codex desktop agent: Resolved integrated review P1 with a red-green truncated-response regression; HTTP transport interruptions now discard partial SSE records and reconnect through the existing bounded backoff.

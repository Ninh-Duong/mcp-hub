# Remove the browser website

Status: complete

## Goal

Remove mcp-hub's browser website and its browser-only runtime features to reduce the installed package and maintenance surface. Keep the desktop Status app and the authenticated loopback API it uses.

## Decisions

- Delete the static website assets, browser pairing/session support, and browser-only job inspection and approval routes.
- Keep the `/dashboard/api/...` route namespace for the desktop app, `hubctl`, and extension APIs.
- Keep MCP job inspection tools. Pending gated writes require a user-provided one-time code; without one, they expire under the existing job window.
- Remove `hubctl open` and the desktop app's browser-opening action.
- Move desktop translations into `desktop/locales/` and package them from there.
- Ignore a legacy `settings.dashboard` object so existing private config does not prevent startup; reject malformed legacy values.

## Implementation steps

1. [x] Remove browser files, sessions, pairing routes, job web routes, and dashboard-specific approval helpers.
2. [x] Keep and test the local APIs needed by the desktop app and enforce admin-token authentication.
3. [x] Remove browser launch actions and `hubctl open`; retain start, stop, restart, status, and `-Ui` behavior.
4. [x] Move desktop locales and update packaging and code-standard checks.
5. [x] Update active documentation, tests, and repository references.
6. [x] Run focused tests, `scripts/package_qc.py`, and `scripts/smoke.py`.

## Acceptance checks

- [x] Browser pages, pairing, browser sessions, and browser-only job routes are absent.
- [x] Desktop API routes and desktop app behavior remain available.
- [x] No active docs or commands direct users to the removed website.
- [x] Package QC passes; smoke check reports all five children connected, with `mcp-read-source` returning its documented `WARN` because its account/project folders are unset.

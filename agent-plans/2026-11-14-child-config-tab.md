# Child config tab and required-settings check

Status: in-progress (T012 pending)

Cross-repo feature. The Hub and desktop app are in this repository; the four child MCP repositories each declare their own required settings. All repositories commit and push directly to `master`, and the user owns them.

## Decisions

- D1: The edit button opens the child's private config file in the default editor. The app has no in-app form.
- D2: Each child repository declares its own required settings in `hub-requirements.json`.
- D3: A child with a missing required setting shows a warning and still starts. It may read the value from its own local files.
- D4: The edit button uses `FluentIcon.EDIT`, not the gear, because the gear already opens the Settings card.
- D5: The Hub does the file work. The app only asks for the path and opens it; it never reads the values.

## User stories

- US1 (P1): As a user, I see at once which connected child is missing a required setting ("Config incomplete" in the Child MCPs card).
- US2 (P1): As a user, I open Settings → Config, see each child's state, and press the edit button to open its file. The Hub creates the file from its template first if it is missing.
- US3 (P2): After I fill the file and restart the Hub, the warning disappears.
- US4 (P2): As a maintainer, I declare a child's required settings in its own repository without changing the Hub.

## Functional requirements

- FR-001: `hub-requirements.json` (version 1) holds `groups`, each with a `label` and `anyOf` alternatives of dotted key paths. A group is satisfied when one alternative has every key filled.
- FR-002: A value is empty when it is missing, `null`, blank, or `<SET_BY_USER>`.
- FR-003: The Hub reads the file from the child's folder (`cwd`, else `../<id>`). The state is `declared`, `none`, or `invalid`.
- FR-004: `GET /dashboard/api/settings` servers include `missing`, `requirements`, and `config_file_exists`.
- FR-005: `GET /dashboard/api/status` includes `config_incomplete` (child id to key paths) for children that started.
- FR-006: `POST /dashboard/api/settings/servers/{id}/config-file` creates the private file from `config/mcps/<id>.example.json` if it is missing, and returns `{"path"}`. Unknown id: 404. `MCP_HUB_CONFIG` set: 409.
- FR-007: A child that starts with a gap is audited as `child_config_incomplete` (server id and key paths only) and still starts.
- FR-008: The desktop Settings card has a fourth tab, Config. Each child has a row with its state and an edit button.
- FR-009: The edit button has an accessible name and tooltip per child, is disabled while its work runs, reports failures and success on its own row, and is disabled with a reason when the config is read-only.
- FR-010: A connected child with a gap shows the warning tone and "Config incomplete" in the Child MCPs card.
- FR-011: Every desktop string lives in `en.json` and `vi.json`.
- FR-012: The four child repositories that have a Hub section each ship a `hub-requirements.json` and a test for it.

## Success criteria

- SC-001: A gap is visible in the desktop app within one status poll after the Hub starts.
- SC-002: No API response, log, or audit event contains a configuration value. Tests check this.
- SC-003: A child with a gap still starts, and `smoke.py` behaves as before.
- SC-004: `package_qc.py` passes with coverage at or above 85 %.
- SC-005: An unchanged poll redraws nothing.

## Edge cases

- No `hub-requirements.json`: state `none`, no warning.
- Malformed `hub-requirements.json`: state `invalid`, error tone, the Hub still starts.
- No private file: the edit button creates it from the template. No template: the Hub refuses with a message on the row.
- `MCP_HUB_CONFIG` set: the Config tab is read-only and the button is disabled with its reason.
- A disabled child: the Config tab lists its gaps, the Child MCPs card does not warn.
- The Hub is not running: the Config tab shows its needs-Hub note and no rows.
- `<SET_BY_USER>` in the file: the existing "credentials required" behavior still applies, and the gap is listed.

## Analysis (requirement to task)

| Requirement | Tasks |
|---|---|
| FR-001 to FR-003 | `child_requirements.py`, `test_child_requirements.py` |
| FR-004, FR-005 | `config.py` (`_switch`, `incomplete`), `hub_settings.py`, `dashboard/settings_api.py`, `dashboard/api.py`, `test_child_config_status.py`, `test_settings_api.py`, `test_dashboard_api.py` |
| FR-006 | `hub_settings.prepare_child_config`, `settings_api.config_file`, `test_child_config_status.py`, `test_settings_api.py` |
| FR-007 | `server.py` (`connect`), `test_audit.py` |
| FR-008, FR-009 | `desktop/config_page.py`, `desktop/settings.py`, `desktop/app.py`, `desktop/hub_api.py`, `desktop/system.py`, `test_desktop_window.py`, `test_desktop_config.py` |
| FR-010 | `desktop/status.py`, `desktop/app.py`, `test_desktop_config.py`, `test_desktop_window.py` |
| FR-011 | `dashboard/locales/en.json`, `vi.json`, `test_dashboard_i18n.py` |
| FR-012 | Child repositories (see the progress log) |

## Tasks

- [x] T001 Write this plan.
- [x] T002 `child_requirements.py` and its tests.
- [x] T003 `config.py`: per-child status, `incomplete`, `child_config_file`.
- [x] T004 `hub_settings.py`: `prepare_child_config`.
- [x] T005 `dashboard/settings_api.py`: new fields, config-file route.
- [x] T006 `dashboard/api.py` and `server.py`: `config_incomplete` and the startup audit.
- [x] T007 Desktop client, status, and Config page with tests.
- [x] T008 Locale keys in `en.json` and `vi.json`.
- [x] T009 Docs: `child-mcp-compatibility.md`, `reference.md`, `security-and-logging.md`, `desktop-app.md`.
- [x] T010 `package_qc.py` passes; `smoke.py` run (see the progress log).
- [x] T011 Child repositories: `hub-requirements.json` and tests (see the progress log).
- [ ] T012 Click-through from the Child MCPs warning to the Config tab, with focus on the child's row (FR-013, not built yet).
- [ ] T013 Set status to `done` after T012.

## Progress log

- 2026-11-14 (agent Claude, mcp-hub): Hub, desktop, docs, and tests done. `package_qc.py` passes (951 unit tests). `smoke.py`: the Hub and four children pass; `mcp-server-logs` fails on its OpenObserve calls ("fetch failed") and `mcp-sqlserver` times out. Both come from reaching the external services, not from this change. The Hub audit shows no `child_config_incomplete` event, because the children do not declare requirements yet. Next: the child repositories.

- 2026-11-14 (agent Claude, child repositories): `mcp-server-logs` (dc03218), `mcp-jira-sync` (64b38a2), `mcp-pr-companion` (77e5017), and `mcp-sqlserver` (0ebe49f) each ship `hub-requirements.json` and a test; each suite passes. Deviations from the first draft: `mcp-server-logs` lists `openobserve.dev.url`, `.dev.email`, `.dev.token` (not `default_env`, which is optional), and the two groups are one `anyOf`. Each child's README or hub doc notes the line. The Hub reads the requirement from the child's folder (`cwd`, else `../<id>`).
- Open issue: `77e5017` in `mcp-pr-companion` was committed with the git identity of the machine's user (a company address, not the GitHub noreply address that the other repositories use). It is on the public `master`. Fixing it needs a rewrite of `master` (amend plus force push), which was not done without the user's go-ahead.

## Version

Ask the user before raising `version` in `pyproject.toml` (see [versioning.md](../docs/versioning.md)). A push to `master` releases only when no Release exists for the version; `v0.5.2` already exists.

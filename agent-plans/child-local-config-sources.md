# Recognize child-local configuration in the required-settings check

Status: in-progress

## Goal

The desktop app shows **Config incomplete** for `mcp-server-logs`, `mcp-pr-companion`, and `mcp-sqlserver` although all three connect and work. Their accounts live in each child's own local files (`config/*.env`, `.env`, `dbconfig.json`), which [Configuration sources](../docs/child-mcp-compatibility.md#configuration-sources) allows. The Hub's check (`child_requirements.missing_keys`) reads only `config/private/mcps/<id>.json`, so it reports a gap that does not exist.

After this change, a required group counts as satisfied when the child itself reports where the group's values come from (`env`, `hub`, `local`, or `default`), and the warning appears only when the group is really `unset`.

## Scope

- Children: `mcp-server-logs`, `mcp-pr-companion`, `mcp-sqlserver` only. `mcp-jira-sync` and `mcp-read-source` are unchanged. They can adopt the same tool later.
- Hub: requirements format, runtime, status/settings API, desktop app, docs, tests.
- Out of scope: entering or editing secret values in the app, and reading child-local files from the Hub.

## Decisions

1. **New standard child tool `config_status`.** It is read-only, makes no network calls, takes no input, and returns names and sources only, never values:
   ```json
   {"groups": {"<group-id>": "env|hub|local|default|unset"}}
   ```
   Each child decides how its own settings map to a group. For example, `mcp-server-logs` reports its account group as set when any OpenObserve environment or Seq has a non-`unset` source.
2. **Group IDs in `hub-requirements.json`.** Each group gains an optional `"id"` (`^[a-z][a-z0-9_]{0,63}$`) that matches a key in the `config_status` output. Initial IDs:
   - `mcp-server-logs`: `log_account`
   - `mcp-pr-companion`: `bitbucket`
   - `mcp-sqlserver`: `mssql_login`

   A group without an `id`, or a child without `config_status`, keeps today's file-only check (backward compatible).
3. **Satisfied means** the group's keys are filled in the Hub file (today's rule), **or** the child reports the group ID with `env`, `hub`, `local`, or `default`. `unset`, an unknown value, a missing ID, a failed call, or a timeout falls back to the file-only result. The Hub never hides a gap because of an error.
4. **When the Hub asks.** After each successful child connect (first start and every reconnect), the Hub calls `config_status` once in a background task, outside the tool-call path, with a timeout from a new setting `childConfigStatusTimeoutSeconds` (default 5). The result is kept in memory per child and replaced on the next connect. There is no polling and no extra refresh API.
5. **Not exposed to agents.** `config_status` is not added to any feature allowlist. The Hub's internal call writes one audit event, `child_config_status`, at INFO level. It records `server_id`, `ok`, and the group IDs with their source words. On failure it records `error_type`.
6. **Display: green only.** A group satisfied by a child-reported source looks like a ready group:
   - the Child MCPs card shows the normal connected state;
   - the Config tab shows **Ready**;
   - no source text is shown.

   The "Missing: <keys>" text stays for groups that are really unset.

## Implementation steps

### Child repositories (follow each child's own `AGENTS.md` and [child-mcp-compatibility.md](../docs/child-mcp-compatibility.md))

1. [x] `../mcp-server-logs`: add `config_status`, reusing the source logic behind `list_log_providers`. Return `log_account`. Add `"id": "log_account"` to `hub-requirements.json`. Add tests.
2. [x] `../mcp-pr-companion`: add `config_status` without the Bitbucket request, reusing the `config_source` logic of `check_bitbucket_connection`. Return `bitbucket`, set from the credentials and workspace sources: `unset` if either is unset. Add `"id": "bitbucket"`. Add tests.
3. [ ] `../mcp-sqlserver`: add `config_status` without opening a connection, reusing the `config_source` logic of `check_connection`. Return `mssql_login`, set from the server and credentials groups. Add `"id": "mssql_login"`. Add tests. **Code and tests done** (`dotnet test`: 338 passed, 4 skipped integration tests). **Not yet live:** `publish/McpSqlServer.exe` is still the old build, because the running child (started by the Hub service) locks `publish/`. Re-publish after the Hub stops that child.

### Hub

4. [x] `child_requirements.py`:
   - parse and validate the optional group `id`. A bad ID makes the file `invalid`, as today.
   - add a function that returns missing keys given a mapping `{group_id: source}`, still names only.

   Unit tests cover: an ID present or absent, each source word, an unknown word, and an invalid ID.
5. [x] `settings.py` and [docs/settings.md](../docs/settings.md): add the timeout (default 5, at most 60). **Deviation:** the key is `children.configStatusTimeoutSeconds`, not `childConfigStatusTimeoutSeconds`, because it sits in the `children` group.
6. [x] `children.py` / `server.py` (**Deviation:** the probe lives in the new module `config_sources.py`, which `server.py` calls after each connect; `children.py` is unchanged. The failure audit event is WARNING, not INFO):
   - after a connect, when the child's tool list contains `config_status`, start a background task that calls it with the timeout and validates the shape: a dict of known group IDs mapped to known source words. Anything else counts as no result.
   - store the result on the runtime and write the `child_config_status` audit event.
   - `config_incomplete()` recomputes the gaps from the file-only gaps plus the stored sources.
   - the startup `child_config_incomplete` warning stays as it is (file-only, before connect). Add `child_config_status` to [docs/security-and-logging.md](../docs/security-and-logging.md).
7. [x] `dashboard/api.py` and `dashboard/settings_api.py`: the settings payload's `missing` list for each child and the status payload's `config_incomplete` both use the source-aware result through public runtime methods. `config.py` stays file-only and does not depend on the runtime.
8. [x] Desktop app: no new UI states are needed (no desktop code changed; the app reads the corrected `missing` and `config_incomplete`). Confirm that `status.py` and `config_page.py` show **Ready** and a green row once `missing` is empty. Update `screenshot.py` sample data if it shows these children.
9. [x] Docs:
   - [docs/child-mcp-compatibility.md](../docs/child-mcp-compatibility.md): the `config_status` convention, group `id`, and the satisfied rule.
   - [docs/desktop-app.md](../docs/desktop-app.md): when **Config incomplete** shows.
   - [docs/mcps/](../docs/mcps/) contracts of the three children: the new tool and the group ID.
   - [docs/reference.md](../docs/reference.md): if the event or setting is listed there.
10. [x] Tests for the Hub (offline, mocked child sessions; `tests/test_config_sources.py`, `tests/test_child_requirements.py`):
    - local source clears the gap;
    - `unset` keeps it;
    - timeout, error, or bad shape keeps it;
    - a child without the tool keeps today's behavior;
    - the reconnect replaces the stored result;
    - the audit event fields contain no values.
11. [ ] Run `uv run python scripts/package_qc.py` and `uv run python scripts/smoke.py`. **package_qc: passed** (1147 tests OK). **smoke: not passing yet**, see Results. Then ask the user whether to raise the version ([docs/versioning.md](../docs/versioning.md#after-a-finished-change-ask-about-the-version)).

## Acceptance checks

- [ ] With accounts only in each child's local files, the desktop app shows `mcp-server-logs`, `mcp-pr-companion`, and `mcp-sqlserver` as connected (green) with no **Config incomplete**, and the Config tab shows **Ready**.
- [ ] With a required group empty everywhere, the child still shows **Config incomplete** and the Config tab lists the missing key names.
- [ ] `config_status` makes no network calls in any of the three children, and none of its output, the audit log, or the API contains a secret value.
- [ ] Agents cannot call `config_status` through the Hub (it is not allowlisted).
- [ ] `mcp-jira-sync` and `mcp-read-source` behave exactly as before.
- [ ] `package_qc.py` passes (done) and `smoke.py` reports all five children connected (open: 5/5 connect, but two data checks fail, see Results).

## Results

- The live Hub accepted both reports: `mcp-server-logs` → `{"log_account": "local"}` and `mcp-pr-companion` → `{"bitbucket": "local"}` (audit events `child_config_status`, `ok: true`). These are the two gaps the screenshot showed.
- Re-published `mcp-sqlserver` (`dotnet publish`, exit 0; the DLL contains `config_status`) after `hubctl stop`, then `hubctl start`. The Hub reports `mcp-sqlserver` → `{"mssql_login": "local"}` too. All three gaps are now covered by the child's own report.
- `smoke.py` (run with `--no-sync`): 5/5 children connected, and the Hub check passes. Two data checks still fail: `mcp-server-logs` `list_openobserve_organizations` (`fetch failed`, about 55 s) and `mcp-sqlserver` `check_connection` (`TimeoutError` at 60 s). The same failures appear in `logs/mcp-hub.jsonl` on earlier days (back to 2026-10-18), and only local calls such as `list_log_providers` succeed. So these look like network or VPN reachability problems, not this change. Confirm on the user's machine.
- Not verified here: the desktop app's view. The Hub's data says the rows should now be green with no "Config incomplete", but please check the Child MCPs card and the Config tab. The Hub's admin token is not something I read.
- Version: not raised (user's choice). Nothing is committed in any repo.

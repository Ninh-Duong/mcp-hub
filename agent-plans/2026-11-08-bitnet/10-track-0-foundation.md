# Track 0: foundation (core hooks, settings, protocols, client)

Status: approved · Branch: `feat/bitnet-foundation` (from `master`) · Runs first, alone · Read [00-overview.md](00-overview.md) first.

Goal: every core change, so tracks A, B and C only add files under `src/mcp_hub/bitnet/` (plus C's app files). With no extension installed, all behavior must stay byte-identical to today.

## Owns

- `.gitignore`, `vendor/README.md`, `AGENTS.md`
- `src/mcp_hub/settings.py`, `src/mcp_hub/extensions.py`
- `src/mcp_hub/full_results.py` (new), `src/mcp_hub/plan_merge.py` (new)
- `src/mcp_hub/server.py`, `src/mcp_hub/policy.py`
- `src/mcp_hub/jobs/runner.py`, `src/mcp_hub/jobs/store.py`
- `src/mcp_hub/agents/config.py`, `src/mcp_hub/agents/specialists.py`
- `src/mcp_hub/bitnet/__init__.py` (skeleton only), `src/mcp_hub/bitnet/client.py`
- `docs/settings.md`, `docs/reference.md`, `docs/security-and-logging.md`, `docs/agents/sql.md` (new), `docs/architecture.md` (planner and condenser hook paragraph)
- the matching tests under `tests/`

## Tasks

### [ ] T001 `vendor/` folder
- `.gitignore`: append
  ```gitignore
  # Downloaded third-party sources, builds and models (created by preflight)
  vendor/*
  !vendor/README.md
  ```
- `vendor/README.md`, about 10 lines:
  - The folder holds third-party sources, builds and models that `python -m mcp_hub.preflight --install` downloads, for example `vendor/bitnet/` when the BitNet extension is on.
  - Nothing in it is tracked.
  - It is safe to delete; it is re-created on the next install.
  - Agents never read or edit it.
- `AGENTS.md`, Private files table: add the row `| vendor/ | Nothing. Third-party sources, builds and models downloaded by preflight (for example vendor/bitnet/); never read, edit, or commit (only vendor/README.md is tracked). |`
- Tests: none. Run `check_leaks.py` (via package_qc) to confirm nothing under `vendor/` is scanned.

### [ ] T002 Settings
- `settings.py`:
  - Add `max_window_hours: int = 168` to `JobSettings`.
  - Add the new class below and the field `bitnet: BitnetSettings = field(default_factory=BitnetSettings)` to `Settings`.
  - Add `("bitnet", "port"): 1024` to `_MINIMUMS`, and `("bitnet", "port"): 65535` to `_MAXIMUMS`.
  - `threads` may be 0: add `("bitnet", "threads"): 0` to `_MINIMUMS`.
  ```python
  @dataclass(frozen=True)
  class BitnetSettings:
      # Local BitNet extension (docs/bitnet.md). Used only when the `bitnet` extension is on.
      port: int = 8766
      threads: int = 0
      model: str = "BitNet-b1.58-2B-4T"
      max_restarts: int = 3
      plan_timeout_seconds: float = 3.0
      condense_timeout_seconds: float = 8.0
      condense_min_chars: int = 6000
      full_result_ttl_seconds: float = 1800.0
      full_result_max_entries: int = 50
  ```
- `docs/settings.md`: document every key (camelCase) with its default, in the same table style as the existing groups.
- Tests (`tests/test_settings.py`):
  - defaults;
  - camelCase parsing of `bitnet.condenseMinChars`;
  - `bitnet.port: 80` rejected;
  - `bitnet.threads: 0` accepted;
  - an unknown key `bitnet.foo` rejected.

### [ ] T003 Protocols (`extensions.py`)
- Add exactly the contracts in overview section 7.3. Keep `ACTIVITY_UI` unchanged. Import `datetime` and `Mapping`.
- `status_flags()` now also reports `bitnet`.
- Check the dashboard and desktop for code that assumes only `activityUi`: grep `status_flags` and `STATUS_FLAGS`. Update their tests if needed.
- Tests (`tests/test_extensions.py`):
  - `KNOWN_EXTENSIONS` contains `bitnet`;
  - `status_flags({"bitnet"}) == {"activityUi": False, "bitnet": True}`;
  - the dataclasses are frozen.

### [ ] T004 `FullResultStore` + `hub__get_full_result`
- New `src/mcp_hub/full_results.py`:
  ```python
  class FullResultStore:
      """Originals of condensed results, in memory only (LRU + TTL). Never written to disk."""
      def __init__(self, max_entries: int, ttl_seconds: float, clock: Callable[[], float] = time.monotonic) -> None: ...
      def put(self, text: str) -> str: ...            # returns "r-" + secrets.token_hex(6); evicts LRU and expired
      def page(self, result_id: str, offset: int = 0, limit: int = 20000) -> dict[str, Any] | None:
          # {"result_id", "offset", "limit", "total_chars", "text", "next_offset" (None at end)}; None if unknown/expired
  ```
- In `server.py`:
  - `HubRuntime.__init__`: add `self.plan_assistant: PlanAssistant None = None`, `self.condenser: ResultCondenser None = None`, and `self.full_results: FullResultStore None = None`.
  - Constant `FULL_RESULT_TOOL = "hub__get_full_result"`.
  - Add a builder `_full_result_tool()` next to `_hub_status_tool()`, with `readOnlyHint: True` and this schema: `result_id` string (required), `offset` integer ≥ 0, `limit` integer from 1 to 50000.
- `list_tools`: when `self.full_results is not None`, return `[*self.public_tools, _full_result_tool()]`. Do not add it to `public_tools`, because the extension is installed after `connect`.
- `call_tool`: add a branch before `PLAN_TOOL`. It runs only when `full_results` is set, and it is not subject to the plan policy, like `hub__status`.
  - Audit it with `tool.call.started` and `tool.call.succeeded` (`server="mcp-hub"`).
  - For an unknown or expired id, return `_error_result("Result expired or unknown; repeat the original call.")`.
- `docs/reference.md`: add the tool, and say it exists only with the `bitnet` extension.
- Tests (`tests/test_full_results.py`, `tests/test_server.py`):
  - put/page round trip and paging (`next_offset`);
  - LRU eviction at `max_entries`;
  - TTL expiry with a fake clock;
  - the tool is absent from `list_tools` when `full_results is None`;
  - an unknown id returns an error result.

### [ ] T005 Condense hook in the direct call path
- In `server.py`, add this method:
  ```python
  async def _maybe_condense(self, name: str, result: CallToolResult, task: str None, request_id: str) -> CallToolResult:
      """Condense a large read-only success result; any failure returns `result` unchanged."""
  ```
- Conditions:
  - `self.condenser` and `self.full_results` are set;
  - `name in self._read_only`;
  - `not result.is_error`;
  - the joined text of the `TextContent` items is at least `settings.bitnet.condense_min_chars` characters.
- Call it as `await asyncio.wait_for(self.condenser.condense(text, task), settings.bitnet.condense_timeout_seconds)`.
- If it returns `None`, raises, or times out, return the original result and audit `source="fallback"`.
- On success:
  - store the original text with `full_results.put(text)`;
  - return `CallToolResult(content=[TextContent(text=condensed.text + footer)], structured_content=None, is_error=False)`;
  - the footer is `f"\n\n[Condensed by mcp-hub: kept {kept} of {total} lines. Full result: {FULL_RESULT_TOOL} result_id=\"{rid}\".]"`.
  - `structured_content` is dropped because it would carry the full data.
- Audit `tool.result.condensed` with only the fields in overview 7.5. Join and split text off the event loop with `asyncio.to_thread` when it is longer than 100 KB.
- Call sites:
  - `_call_planned`: after `policy.record(...)`, `result = await self._maybe_condense(name, result, plan.task if plan else None, request_id)`. It goes before the policy-warning prepend.
  - `_call_batch.call_child`: the same, after `policy.record`, with `item_id`.
- Add `task: str = ""` to `Plan` in `policy.py`, and set it in `parse_plan` from the validated `task`. Keep it in memory only and never audit it.
- Document `tool.result.condensed` in `docs/security-and-logging.md` and `docs/reference.md`.
- Tests (`tests/test_server.py` with a fake condenser):
  - small, error and write-tool results come back unchanged (the same object or equal);
  - a large result is condensed, has the footer, and its id resolves through `hub__get_full_result`;
  - a condenser that raises or sleeps past the timeout returns the original, with audit `source=fallback`;
  - the audit line contains no result text;
  - in a batch, only the large item is condensed;
  - with `condenser=None`, the code path is never entered (regression).

### [ ] T006 Plan assistant hook + window in plans
- New `src/mcp_hub/plan_merge.py`:
  ```python
  def parse_window(since: Any, until: Any, max_hours: int) -> tuple[datetime, datetime] | None:
      """Both None -> None. ISO-8601 strings (Z or offset) -> aware UTC datetimes. Raise ValueError when only one
      is given, since >= until, or the span exceeds max_hours."""

  def merge_plan_arguments(arguments: Mapping[str, Any], hints: PlanHints None) -> tuple[dict[str, Any], bool]:
      """Copy of arguments with missing playbook/servers/env/since/until filled from hints; (merged, used_any)."""
      # Never overwrite a key the caller gave. Never add servers when the caller gave a playbook (the playbook's servers apply).
  ```
- `policy.py`:
  - `_PLAN_SCHEMA` gets `since` and `until` (string, format `date-time`, described as "UTC window for log/SQL time filters").
  - `Plan` gets `window: tuple[datetime, datetime] | None = None`.
  - `parse_plan` validates the window with `parse_window(..., max_hours)`. Pass `max_hours` through `PlanCatalog` as a new field `max_window_hours: int = 168`. A `ValueError` becomes `PolicyRefused`.
  - `plan_summary` adds `Time window: <since ISO> to <until ISO> (UTC). Use exactly this window for every log and SQL time filter.` when a window is set.
- `server._create_plan`, before `self.plans.create`:
  ```python
  arguments, assist = await self._assist_plan(arguments)  # assist: "off" | "rules" | "bitnet" | "fallback"
  ```
  - `_assist_plan` returns `(arguments, "off")` when `plan_assistant is None` or `task` is not a non-empty string.
  - Otherwise it builds a `PlanContext`:
    - `playbooks`: `{id: pb.when}`;
    - `servers`: the connected children;
    - `envs`: the allowed envs of `arguments["project"]` when it is a registered project, else `()`;
    - `now`: `datetime.now(UTC)`.
  - It awaits `hints` under `wait_for(planTimeoutSeconds)`, then merges.
  - An exception, a timeout, or `None` means `"fallback"`. A merge that used nothing keeps the hint source only if hints existed; otherwise `"fallback"`.
  - Validate hint values before the merge: an env not in `envs`, servers not connected, or an unknown playbook are dropped.
  - Add `assist=assist` to the `hub.plan.created` audit call. Document the field.
- Tests:
  - `tests/test_plan_merge.py`: caller keys win, a playbook blocks hinted servers, window parsing including `Z`, the window limit, and one-sided windows.
  - `tests/test_policy.py`: the window appears in the summary, and an over-limit window is refused.
  - `tests/test_server.py` with a fake assistant:
    - fills servers, env and window;
    - a raising assistant gives `assist=fallback` and the plan is still refused or accepted exactly as without an assistant;
    - no assistant gives `assist=off`;
    - a hinted env outside the project is dropped.

### [ ] T007 Window in jobs
- `JobRunner.submit_issue(..., since: str None = None, until: str None = None)`:
  - validate with `parse_window(since, until, self.limits.max_window_hours)`;
  - store the window as ISO strings.
- `JobStore.create_job(..., since=None, until=None)`: put `since` and `until` into the sealed request dict.
- `_JOB_TOOLS` schema for `hub__submit_issue`: add `since` and `until` (string, `date-time`).
- `_specialist_task`: add `"window": {"since": ..., "until": ...}` when both are set, else omit the key, so the payload is byte-identical to today without a window.
- Specialist instruction: append one sentence to `_DEFAULT_PROMPT` and to each `docs/agents/*.md`: "When the task has a `window`, use exactly that UTC window for every log and SQL time filter."
- Tests (`tests/test_jobs*.py`):
  - the window is stored and reaches `_specialist_task`;
  - with no window, the task JSON equals today's;
  - an invalid window raises `ValueError` and the tool returns `is_error`.

### [ ] T008 `sql` specialist role
- `agents/config.py`: `AGENT_ROLES = ("coordinator", "logs", "source", "jira", "pr", "sql")`.
- `agents/specialists.py`: `ROLE_PREFIXES["sql"] = "sql__"`.
- `_roles_for`: append `"sql"` when the question matches `\b(sql|database|db|table|query|stored procedure|bang|du lieu|co so du lieu)\b`, matched against the question folded to ASCII (casefold + strip diacritics, U+0111 (d with stroke) -> `d`; see decision 13 in the overview). Put a small `fold()` helper in `plan_merge.py`; track A defines its own in `bitnet/rules.py`.
- New `docs/agents/sql.md`, in the style of `docs/agents/logs.md`:
  - read-only `sql__*` tools only;
  - start with `sql__search_context` or `sql__find_columns`;
  - always bound queries (`TOP`/limit);
  - respect the window;
  - cite evidence IDs.
- Check `scripts/private_config.py agent-status` and `config/agents.example.json` (if present) for role lists, and add `sql` there.
- Tests:
  - `tools_for_role("sql", tools)` returns only `sql__` tools that are read-only;
  - `_roles_for` adds `sql` for "loi du lieu bang orders" and for its accented form written with `\u` escapes, and not for "loi login".

### [ ] T009 Planner and condenser in jobs
- `JobRunner.__init__(..., plan_assistant=None, condenser=None, summarizer=None)`: store them. `summarizer` is used by track D only.
- Add `async def _plan_job(self, job_id, request) -> tuple[list[str], dict[str, Any]]`, called at the start of `_execute_job`. It returns the roles and the request, possibly with `env`, `since` and `until` filled.
  - No assistant: `(_roles_for(request), request)` and the store event `job.planned` with `tool="off"`.
  - With an assistant:
    1. `hints` under `wait_for(bitnet planTimeoutSeconds)`; pass that value into `JobRunner` as `plan_timeout_seconds: float = 3.0`.
    2. Map the hinted servers to roles with `plan_merge.roles_for_servers(servers, runtime.routes)`: the role whose `ROLE_PREFIXES` value prefixes any tool routed to that server.
    3. Roles = `_roles_for(request)` ∪ the hinted roles, intersected with the configured `AGENT_ROLES` minus `coordinator`.
    4. Fill the window only if the request has none.
    5. Never change `env` here, because env was validated at submit.
    6. Write the event `job.planned` with `tool=source`, or `fallback` on error or timeout.
- `_investigate` takes the roles from `_plan_job` instead of calling `_roles_for` directly.
- `SpecialistExecutor(..., condenser=None, condense_min_chars=6000, condense_timeout_seconds=8.0)`. In `_run_tool_call`, when the result is not an error and its text is at least `condense_min_chars`:
  - call the condenser under its timeout;
  - on success, use `condensed.text` plus `f"\n[Condensed: kept {k} of {n} lines; full evidence {source_id}.]"` as the content passed to `minimize_model_content`;
  - otherwise keep today's path.
  The full result is already saved encrypted by `save_result`.
- Tests:
  - no assistant gives roles equal to `_roles_for` and event `off` (regression);
  - a fake assistant hinting `mcp-sqlserver` adds `sql`;
  - a raising assistant gives `fallback`;
  - a window is filled only when absent;
  - a fake condenser in the specialist shrinks content, and its failure keeps today's content.

### [ ] T010 BitNet client + package skeleton
- `src/mcp_hub/bitnet/__init__.py`: a docstring only ("Optional local BitNet extension; see docs/bitnet.md"), plus `def install(*args, **kwargs) -> None: raise NotImplementedError` with a comment that track D replaces it. Do not import it anywhere yet.
- `src/mcp_hub/bitnet/client.py`:
  ```python
  class BitnetClient:
      def __init__(self, port: int, *, transport: httpx.AsyncBaseTransport None = None) -> None: ...
      async def complete_json(self, system: str, user: str, *, max_tokens: int = 256, timeout: float = 3.0) -> dict[str, Any] | None:
          """POST http://127.0.0.1:{port}/v1/chat/completions (temperature 0). Return the first JSON object found in
          the reply text, or None on any HTTP, timeout, or parse error. Never raises."""
      async def complete_text(self, system: str, user: str, *, max_tokens: int = 512, timeout: float = 20.0) -> str None: ...
      async def healthy(self, timeout: float = 1.0) -> bool:   # GET /health -> 200
  ```
  Use one `httpx.AsyncClient` per call or a lazily created shared one closed by `aclose()`. Check that `httpx` is a direct dependency in `pyproject.toml`; if it is only transitive, add it, and update `uv.lock` with `uv lock`.
- Tests (`tests/test_bitnet_client.py`, with `httpx.MockTransport`):
  - a valid JSON reply;
  - JSON wrapped in prose;
  - an invalid reply gives `None`;
  - an HTTP 500 gives `None`;
  - a timeout gives `None`;
  - `healthy` returns true or false.

## Acceptance for track 0
- With no extension installed, the full test suite passes unchanged and `smoke.py` passes.
- Every new hook has a fallback test and a regression test.
- `docs/settings.md`, `docs/reference.md` and `docs/security-and-logging.md` list every new key, tool, field and event, so `check_docs.py` passes.

## Blocked

- (none)

## Progress log

- 2026-11-08 Claude (Opus): track written. Next: implement T001 → T010 in order.

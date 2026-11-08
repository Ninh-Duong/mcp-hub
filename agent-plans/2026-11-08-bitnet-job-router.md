# Optional BitNet job router

Status: draft

## Context

The user wants Microsoft BitNet (repo `microsoft/BitNet`, runtime `bitnet.cpp`, model `BitNet-b1.58-2B-4T`, 1.58-bit, CPU-only, about 1.2 GB GGUF) to run locally beside the Hub. Anyone who clones mcp-hub should be able to switch it on and get better investigation jobs (`hub__submit_issue`):

- **More precise:** "check production hôm nay" should become env `prod` and an absolute window covering the last 24 hours, so specialists do not scan too widely.
- **Smarter routing and faster:** the question decides which specialists run (for example `logs` + `sql` for a data issue), and they run in parallel.
- **Optional:** a switch in the desktop app. Off means the Hub behaves exactly as today. On means the optimized flow below.

### What the code does today (verified 2026-11-08)

- Specialists already run in parallel: one `asyncio.create_task` per role in `JobRunner` (`src/mcp_hub/jobs/runner.py`, the method documented "Run the specialists for at most the job window").
- Role choice is a keyword rule, `_roles_for` in `src/mcp_hub/jobs/runner.py`. It always runs `logs` + `source`, adds `jira` only when `issue_key` is given, and adds `pr` on an issue key or the word "pr" / "pull request".
- The specialist task, `_specialist_task` in the same file, sends only `project`, `question`, `environment`, `issue_key`, and `rcid`. It has **no time window**.
- `submit_issue(project, question, env, issue_key, rcid)`: when `env` is absent it uses the project default, or the job goes to `awaiting_input`.
- There is no `sql` specialist. Roles live in `AGENT_ROLES` (`src/mcp_hub/agents/config.py`: coordinator, logs, source, jira, pr) and tool prefixes in `ROLE_PREFIXES` (`src/mcp_hub/agents/specialists.py`).
- Providers are cloud only: `openai` (Responses API), `anthropic`, `gemini` (`src/mcp_hub/agents/providers.py`). They are configured per role in `config/private/agents.json` (never open it; use `scripts/private_config.py agent-status`).
- Extension mechanism: `src/mcp_hub/extensions.py` (`KNOWN_EXTENSIONS`, `STATUS_FLAGS`, `saved_extensions` reads the top-level `extensions` list of the Hub config). `service.py` installs extensions at start. The desktop app toggles them through `hub_settings.py`. Today's `set_activity_ui` **overwrites** the list with `[activity-ui]`, so it must be generalized before a second extension exists.
- Optional runtime installs belong in preflight (`python -m mcp_hub.preflight --install`, run by `hubctl start`). Never add ad-hoc install steps elsewhere (AGENTS.md "One start path").

## Decisions

Agreed with the user. Do not re-discuss.

1. **BitNet only routes and extracts.** It turns the question into `{roles, env, since, until, service, rcid}`. It never calls MCP tools, because a 2B model is poor at tool calling. Cloud providers stay the specialists and the coordinator.
2. **Rules first, BitNet second.** A deterministic parser handles common phrases first: "hôm nay/today", "24h/24 giờ/last 24 hours", "hôm qua/yesterday", "production/prod", "staging/stg", "dev", rcid/GUID patterns, Jira keys, and service names that match the project. BitNet is asked only for what the rules leave unresolved.
3. **It is an extension named `bitnet`,** the same mechanism as `activity-ui`. It is saved in the Hub config's `extensions` list and toggled in the desktop app (⚙ Settings → Hub). Core modules and `jobs` never import it; they see only a `JobPlanner` protocol in `extensions.py`.
4. **Never worse than today.** When the extension is off, BitNet is unreachable, it times out, or it returns invalid output, the planner returns `None` and the runner uses `_roles_for` with no window. A job never fails because of BitNet.
5. **Explicit arguments win.** `env`, `issue_key`, `rcid`, `since`, and `until` given by the caller override anything the planner infers. The inferred env must pass `validate_project_env`. Inferred roles are intersected with the configured roles.
6. **Scope: investigation jobs only.** Direct client use (`hub__plan` from Claude/Codex) is unchanged, because the client LLM already understands the question.
7. **The runtime and model live outside the repo,** under `%LOCALAPPDATA%\mcp-hub\bitnet\`. They are installed once by preflight when the extension is on. Never commit binaries or models.

## Design

### Flow (extension on)

```text
hub__submit_issue(question, [env, since, until, ...])
  -> JobRunner._run_job
       -> planner.plan(question, project envs, now)        # asyncio.wait_for(plannerTimeoutSeconds)
            1. rules.parse(question, now)                   # deterministic, no model
            2. if unresolved: bitnet client -> llama-server /v1/chat/completions (JSON only)
            3. validate -> JobPlan | None
       -> merge(explicit args > plan > defaults)
       -> audit job.planned (source=rules|bitnet|fallback, roles, has_window)
       -> run selected specialists in parallel, each task carries since/until
       -> coordinator synthesizes (cloud, unchanged)
```

### JobPlanner contract (`extensions.py`)

```python
@dataclass(frozen=True)
class JobPlan:
    roles: tuple[str, ...] = ()
    env: str | None = None
    since: datetime | None = None   # UTC
    until: datetime | None = None   # UTC
    service: str | None = None
    rcid: str | None = None
    source: str = "rules"           # rules | bitnet

class JobPlanner(Protocol):
    async def plan(self, question: str, envs: tuple[str, ...], now: datetime) -> JobPlan | None: ...
```

### BitNet prompt contract

- System prompt: return one JSON object only, with no prose. The fields are `roles` (enum of configured roles), `env` (enum of the project's allowed envs, or null), `window` (`{"hours": int}` or `{"from": "...", "to": "..."}` or null), `service`, and `rcid`.
- Use llama-server grammar/JSON mode if the bitnet.cpp build supports it. Otherwise parse leniently and validate strictly.
- Reject (return `None` from the BitNet step) on any schema error, an unknown role or env, or a window over `maxWindowHours`.
- Never send secrets. The question is already user text; redact it with the Hub redactor before sending, as for cloud providers.

## Out of scope

- BitNet as coordinator summarizer (no cloud key needed). Add it when model quality is acceptable.
- Local pre-summarizing or redaction of tool results before they reach a cloud model (a privacy win; a good next feature).
- Suggesting a `hub__plan` playbook for direct client calls.
- Running BitNet on GPU or with other models.

## Tasks

- [ ] **1. Time window in jobs** (useful even without BitNet). Files: `src/mcp_hub/jobs/runner.py`, `src/mcp_hub/jobs/store.py`, the `hub__submit_issue` tool schema, `src/mcp_hub/agents/specialists.py`.
  - Add optional `since`/`until` (ISO-8601 UTC) to `submit_issue` and `hub__submit_issue`. Validate `since < until` and a span of at most `settings.jobs.maxWindowHours`.
  - Store them in the encrypted request and add them to `_specialist_task`.
  - The specialist system prompt says: when a window is given, use exactly it for log and SQL time filters.
- [ ] **2. `sql` specialist role.** Files: `src/mcp_hub/agents/config.py` (`AGENT_ROLES`), `src/mcp_hub/agents/specialists.py` (`ROLE_PREFIXES["sql"] = "sql__"`), new `docs/agents/sql.md` (required by `scripts/check_docs.py`), and `scripts/private_config.py` if it lists roles.
  - Offer read-only `sql__*` tools only (no writes, the same policy as other specialists).
  - `_roles_for` adds `sql` on keywords (sql, database, db, table, query, bảng, dữ liệu).
- [ ] **3. Planner hook in core.** Files: `src/mcp_hub/extensions.py` (`JobPlan`, `JobPlanner`), `src/mcp_hub/jobs/runner.py`.
  - `JobRunner.__init__` takes `planner: JobPlanner | None = None`.
  - In `_run_job`: call the planner under `asyncio.wait_for`, merge (Decision 5), then pick roles from the plan or from `_roles_for`.
  - New audit event `job.planned` (fields: `source`, `roles`, `has_window`; never the question).
- [ ] **4. Extension package `src/mcp_hub/bitnet/`.**
  - `rules.py`: the deterministic parser (Vietnamese + English) that computes absolute UTC windows from `now`.
  - `client.py`: async `httpx` POST to `http://127.0.0.1:<port>/v1/chat/completions`, bounded timeout, strict JSON validation.
  - `sidecar.py`: start, health-check, and stop `llama-server` from the bitnet.cpp build as a child process. Restart it at most `maxRestarts` times. Do all process work in a thread or background task, never blocking the event loop. Stop it when the Hub stops.
  - `planner.py`: rules → BitNet → `None`; implements `JobPlanner`.
  - `__init__.py`: `install(service)` creates the sidecar and planner and returns status fields for the dashboard (`bitnet: ready|starting|installing|unavailable`).
  - Register `BITNET = "bitnet"` in `KNOWN_EXTENSIONS` and `STATUS_FLAGS` (`bitnet`) in `extensions.py`. `service.py` installs it only when it is saved or launched, and passes the planner to `JobRunner`.
  - Declare `httpx` explicitly in `pyproject.toml` if it is only a transitive dependency today.
- [ ] **5. Settings.** `src/mcp_hub/settings.py` + `docs/settings.md`: group `bitnet` with `port` (default 8766), `plannerTimeoutSeconds` (3), `threads` (0 = auto), `model` (`BitNet-b1.58-2B-4T`), `maxRestarts` (3). Add `jobs.maxWindowHours` (168).
- [ ] **6. Install via preflight.** New `scripts/install_bitnet.ps1`, called by `src/mcp_hub/preflight.py --install` only when `bitnet` is saved on.
  - Check or install the requirements: VS 2022 Build Tools with the C++ + clang workload, cmake, and Python 3.9+. Use winget where possible.
  - Clone `microsoft/BitNet` with submodules into `%LOCALAPPDATA%\mcp-hub\bitnet\src`, then run `setup_env.py` to download the GGUF model and build with the `i2_s` quant.
  - Re-running it is a no-op when the build and model already exist.
  - On failure, print `bitnet: unavailable (<reason>)` and exit non-fatally. The Hub starts normally in fallback mode.
- [ ] **7. Desktop toggle.** Files: `src/mcp_hub/hub_settings.py`, the Hub settings HTTP route, `src/mcp_hub/desktop/` (Settings → Hub tab), and `src/mcp_hub/dashboard/locales/*.json`.
  - Generalize `set_activity_ui` into `set_extension(name, enabled)`, which adds or removes one name and keeps the others. Keep `set_activity_ui` as a thin wrapper or update its callers.
  - Add `bitnet: bool` to `HubSettings`.
  - Add a "BitNet (local AI)" switch plus a status line. Switching it on saves the extension and asks to restart the Hub, which runs preflight install.
- [ ] **8. Docs.** New `docs/bitnet.md` (what it does, requirements, disk/RAM, fallback, how to switch it off), `docs/reference.md` (new args, status fields, routes), `docs/security-and-logging.md` (`job.planned`), `docs/desktop-app.md` (switch), `docs/architecture.md` (planner hook), and an AGENTS.md row if needed.
- [ ] **9. Tests** (offline; mock the sidecar, httpx, and the clock).
  - `rules.py` table tests: "check production hôm nay" → env prod, 24 h window; "lỗi sql orders-api dev hôm qua" → roles include sql, env dev, yesterday's window; text with no hints → unresolved.
  - Planner: explicit args win; an invalid env is rejected; a timeout or exception → fallback; bad BitNet JSON → discarded.
  - Runner: with no planner, `_roles_for` is used unchanged (regression); the window reaches `_specialist_task`; `job.planned` is written.
  - `set_extension` keeps the other extensions; failing to start the sidecar leaves the Hub running.

## Verification

- `uv run python scripts/package_qc.py` (lint, tests, coverage ≥ 85, leaks, standards, docs)
- `uv run python scripts/smoke.py`
- Manual:
  1. Switch on BitNet in the desktop app and restart. The status goes installing → ready.
  2. `hub__submit_issue` "check production hôm nay". `hub__get_job` shows the planned roles and window, and `logs/mcp-hub.jsonl` has `job.planned source=rules`.
  3. Ask a question the rules cannot resolve. Expect `source=bitnet`.
  4. Kill llama-server and submit again. Expect `source=fallback` and the job still completes.
  5. Switch BitNet off. Jobs behave as before.

## Open questions

- Should BitNet also auto-pick the project alias when the user does not name one, or always require it? (Current answer: require it, as today.)
- Keep the first version's rules/BitNet output to roles + env + window only, and add `service`/`rcid` later?

## Progress log

- 2026-11-08 Claude (Opus): discussed with user; verified current job flow in code; wrote this plan as draft. Next: user reviews, sets `Status: approved`, then any agent implements tasks 1 → 9 in order.

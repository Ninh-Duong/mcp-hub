# BitNet local orchestrator: overview

Status: done

Read this file, then **only** your own track file. Do not re-discuss the Decisions. Do not read other track files unless your track file tells you to.

| File Track Runs |
|---|---|---|
| [10-track-0-foundation.md](10-track-0-foundation.md) | 0: core hooks, settings, protocols, client first, alone |
| [20-track-a-planning.md](20-track-a-planning.md) | A: rules + plan assistant after 0, in parallel with B and C |
| [30-track-b-condense.md](30-track-b-condense.md) | B: result condenser after 0, in parallel with A and C |
| [40-track-c-runtime.md](40-track-c-runtime.md) | C: sidecar, installer, desktop switch after 0, in parallel with A and B |
| [50-track-d-integrate.md](50-track-d-integrate.md) | D: wiring, offline mode, client docs, final checks last, after A, B and C are merged |

## 1. Context

mcp-hub fronts child MCPs (logs, jira, pr, source, sql) for AI clients (Claude, Codex) and runs cross-system investigation jobs with cloud models. Three costs are high today:

- **Plans are hand-written.** Clients must give `hub__plan` the playbook, servers, env and steps themselves. Jobs choose specialists with a keyword rule (`_roles_for` in `src/mcp_hub/jobs/runner.py`) and have no time window, so specialists scan too wide.
- **Large tool results.** Direct calls return the child result unchanged, so a 50 KB log dump goes straight into the client's context. Job specialists hard-cut every result at 6000 characters (`MAX_TOOL_RESULT_CHARS` in `src/mcp_hub/agents/specialists.py`), so evidence after the cut is lost, and every round resends it to the cloud model.
- **No offline path.** A job without cloud keys cannot summarize anything.

Microsoft BitNet (`microsoft/BitNet`, runtime bitnet.cpp, model `BitNet-b1.58-2B-4T`, about 1.2 GB GGUF, CPU only, 4096-token context) runs locally next to the Hub as an optional extension. It sits between the AI client and the child MCPs:

1. **Plan:** it fills in what a `hub__plan` call (or a job) leaves out: playbook, servers, env, time window.
2. **Condense:** it shrinks large read-only results before they reach the client or a cloud model. The full original stays retrievable.
3. **Offline draft:** when no cloud key is configured, it writes a draft job summary.

One switch in the desktop app turns it on. It is off by default, and off means exactly today's behavior.

## 2. User stories (Spec Kit format)

- **US1 (P1) Standard plan.** As a client agent, I send `hub__plan` with only `task` and `project` ("check production hom nay loi sql"). The Hub returns a plan with servers, env `prod`, and a 24-hour UTC window.
  - *Acceptance:* the plan summary lists the filled servers, env and window. The audit event `hub.plan.created` has `assist=rules|bitnet`. Arguments the client did give are never changed.
- **US2 (P1) Condensed results.** As a client agent, a 50 KB log result arrives as a short, deduplicated result with a footer naming a `result_id`. `hub__get_full_result` pages through the original.
  - *Acceptance:* small results, error results and results of write tools come back byte-identical to today. The full text is never written to disk.
- **US3 (P2) Smarter jobs.** As a user of `hub__submit_issue`, the job picks its roles (including the new `sql` role), env and window from the question, and its specialists see condensed evidence instead of a hard cut.
- **US4 (P3) Offline draft.** With BitNet on and no cloud provider configured, a job still runs the playbook's fixed read steps and returns a summary labeled `Draft (local model)`.
- **US5 (P1) Switch.** In the desktop app (⚙ Settings → Hub), I turn BitNet on. The Hub restarts, preflight installs the runtime into `vendor/bitnet/`, and the status shows `installing`, then `ready`. Off, or `unavailable`, means today's behavior.

## 3. Functional requirements

| ID Requirement |
|---|---|
| FR-001 Every BitNet behavior is behind the `bitnet` extension. With it off, Hub output is byte-identical to today (regression tests prove it). |
| FR-002 A deterministic rule parser runs first. BitNet is asked only for the fields the rules leave unresolved. |
| FR-003 Arguments the caller gave always win over inferred values. An inferred env must be one of the project's allowed envs. Inferred servers must be connected children, and inside the playbook's servers when there is a playbook. |
| FR-004 Any BitNet failure (unreachable, timeout, invalid JSON, out-of-enum value) falls back to today's path and is audited as `fallback`. No call or job fails because of BitNet. |
| FR-005 | `hub__plan` and `hub__submit_issue` accept optional `since` and `until` (ISO-8601, UTC). The span must be greater than 0 and at most `jobs.maxWindowHours`. |
| FR-006 The plan summary and the specialist task carry the window. Specialists are told to use exactly that window for log and SQL time filters. |
| FR-007 Only read-only tool results that are not errors and have text of at least `bitnet.condenseMinChars` are condensed. |
| FR-008 Condensing is extractive. Step A is deterministic: dedupe lines and group them by template with counts. Step B asks BitNet only for the line numbers to keep. The model never rewrites evidence. |
| FR-009 Originals live in an in-memory `FullResultStore` (LRU, `fullResultMaxEntries`, TTL `fullResultTtlSeconds`), read through `hub__get_full_result(result_id, offset, limit)`. An expired id returns a clear error. |
| FR-010 Jobs gain a `sql` specialist role that is offered read-only `sql__*` tools only. |
| FR-011 The runtime installs only through `python -m mcp_hub.preflight --install`, only when `bitnet` is saved on, into `vendor/bitnet/`. Re-running it after a successful install does nothing. A failed install never stops the Hub. |
| FR-012 The sidecar `llama-server` binds to 127.0.0.1 only, is health-checked, is restarted at most `bitnet.maxRestarts` times, and stops with the Hub. No process or network work runs on the event loop. |
| FR-013 Audit events never contain the task, question, or result text. |
| FR-014 The desktop app shows a BitNet switch and a status (`off`, `installing`, `ready`, `unavailable`). `set_extension` keeps the other saved extensions. |

## 4. Success criteria

- SC-001: `package_qc.py` and `smoke.py` pass after every task. Coverage stays ≥ 85 %.
- SC-002: For "check production hom nay" the rules alone produce env `prod` and a 24 h window, with no model call (unit test).
- SC-003: Condensing a 5,000-line log with 20 distinct templates returns at most `condenseMinChars` characters, and every distinct error template survives step A (unit test).
- SC-004: With llama-server killed, every manual check in section 9 still passes with `assist=fallback`.
- SC-005: Planning adds at most `bitnet.planTimeoutSeconds` to a `hub__plan` call. Condensing adds at most `bitnet.condenseTimeoutSeconds` to a call.

## 5. Edge cases

- The question names two envs ("compare dev and prod"): leave env unresolved and do not guess.
- A relative window crosses midnight or DST: always compute in UTC from an injected `now`.
- The window is larger than `maxWindowHours`: reject an explicit window with a clear `Policy:` or `ValueError` message, and drop an inferred one.
- The result is JSON, not log lines: step A works on lines of the pretty-printed text. If there are fewer than 50 lines, condensing is skipped.
- BitNet returns line numbers out of range: drop them. If nothing is left, use the step A output.
- `hub__get_full_result` is called after the Hub restarted: answer "result expired; repeat the original call".
- `MCP_HUB_CONFIG` is set: the desktop switch is read-only (the same rule as today's settings).

## 6. Decisions (agreed with the user; do not re-discuss)

1. BitNet never calls MCP tools. It plans, picks line numbers, and writes offline drafts only.
2. Rules first, BitNet second. Every model output is strictly validated, and invalid output is discarded.
3. BitNet is the extension `bitnet`. Core modules (`server`, `jobs`, `agents`, `extensions`, `policy`) see only the Protocols in `extensions.py` and never import `mcp_hub.bitnet`. Only `service.py` imports it, when the extension is requested.
4. Never worse than today (FR-001, FR-004).
5. The scope covers direct client calls (`hub__plan`, child tools, `hub__batch`) and investigation jobs.
6. Condensing applies to large read-only results only. Originals stay in RAM only. Job evidence is already stored encrypted per source ID, so jobs do not use the RAM store.
7. The project alias is always required. BitNet never picks a project.
8. v1 extracts playbook, servers or roles, env and window. `service` and `rcid` extraction is out of scope.
9. Off by default. The first switch-on triggers the install through preflight. `Setup.cmd` never installs BitNet.
10. Downloaded sources, the build and the model live in `vendor/bitnet/`. `vendor/` is git-ignored except `vendor/README.md`.
11. The sidecar uses only 127.0.0.1, so no data leaves the machine. The 4096-token context means chunking and hard time budgets.
12. Agents commit after each task that passes QC, on their track branch. **This is an explicit exception, granted by the user, to the repo rule "commit only when the user asks".** Only the user merges into `master`.
13. Tracked files must not contain Vietnamese letters (`check_code_standards.py`). Vietnamese phrases are matched by folding the input to ASCII (casefold, NFD, drop combining marks, U+0111 (d with stroke) -> `d`) and using ASCII patterns (`hom nay`, `du lieu`). Tests write accented input with `\u` escapes.

## 7. Architecture

### 7.1 Flow

```text
client ──hub__plan(task, project[, since, until, ...])──► HubRuntime._create_plan
          └─ if runtime.plan_assistant: hints = await wait_for(assistant.hints(ctx), planTimeoutSeconds)
             merged = merge_plan_arguments(arguments, hints)          # caller args win
             policy.parse_plan(merged, catalog)                       # unchanged validation
client ◄── plan summary (+ "Time window: <since> → <until> UTC")
client ──child tool / hub__batch──► HubRuntime._call_planned / _call_batch ──► child
          └─ result = await self._maybe_condense(name, result, plan)
               only if runtime.condenser and read-only and not is_error and len(text) ≥ condenseMinChars
               condensed = await wait_for(condenser.condense(text, plan.task), condenseTimeoutSeconds)
               result_id = full_results.put(text) → condensed text + footer
client ──hub__get_full_result(result_id, offset, limit)──► FullResultStore page
hub__submit_issue ──► JobRunner._execute_job
          └─ plan_job(): assistant hints → roles/env/window (explicit args win) → store event job.planned
             specialists (parallel) ── SpecialistExecutor uses condenser instead of the hard cut
             coordinator (cloud) │ no cloud provider + summarizer → offline draft
```

### 7.2 Modules

| Module Layer Owner track Purpose |
|---|---|---|---|
| `src/mcp_hub/extensions.py` | core 0 Protocols and dataclasses below; `BITNET` name and status flag |
| `src/mcp_hub/full_results.py` (new) | core 0 | `FullResultStore` (LRU + TTL, in memory) |
| `src/mcp_hub/plan_merge.py` (new) | core 0 | `merge_plan_arguments`, `parse_window`, server-to-role mapping |
| `src/mcp_hub/server.py` | core 0 hooks in `_create_plan`, `_call_planned`, `_call_batch`; `hub__get_full_result` |
| `src/mcp_hub/policy.py` | core 0 | `since`/`until` in the plan schema; `Plan.window`; window line in `plan_summary` |
| `src/mcp_hub/jobs/runner.py`, `jobs/store.py` | core 0 (D for offline) | window, `sql` role, planner hook, condenser in specialists, offline branch |
| `src/mcp_hub/agents/config.py`, `agents/specialists.py` | core 0 | `sql` role; condenser use |
| `src/mcp_hub/settings.py` | core 0 | `BitnetSettings`, `jobs.max_window_hours` |
| `src/mcp_hub/bitnet/client.py` | extension 0 | `BitnetClient.complete_json` |
| `src/mcp_hub/bitnet/rules.py`, `planner.py` | extension A | `parse_rules`, `BitnetPlanAssistant` |
| `src/mcp_hub/bitnet/condense.py` | extension B | `BitnetCondenser` |
| `src/mcp_hub/bitnet/sidecar.py` | extension C | `Sidecar` (llama-server process) |
| `scripts/install_bitnet.ps1`, `src/mcp_hub/preflight.py` | ops C install |
| `src/mcp_hub/hub_settings.py`, `dashboard/settings_api.py`, `desktop/*`, `dashboard/locales/*` | app C switch |
| `src/mcp_hub/bitnet/__init__.py`, `bitnet/summarize.py`, `service.py` | extension D (skeleton by 0) | `install()`, offline draft |

The dependency direction stays as in AGENTS.md: `extensions` and the new core modules depend only on `config`/`settings`. `server` and `jobs` use them. `bitnet/*` may import core modules; core never imports `bitnet`.

### 7.3 Contracts (`src/mcp_hub/extensions.py`, written by track 0, frozen afterwards)

```python
BITNET = "bitnet"
KNOWN_EXTENSIONS = frozenset({ACTIVITY_UI, BITNET})
STATUS_FLAGS = {ACTIVITY_UI: "activityUi", BITNET: "bitnet"}

@dataclass(frozen=True)
class PlanContext:
    task: str                                  # the user's request (never logged)
    playbooks: Mapping[str, str]               # playbook id -> "when to use"
    servers: tuple[str, ...]                   # connected child server IDs
    envs: tuple[str, ...]                      # the project's allowed envs ("" tuple when no project)
    now: datetime                              # timezone-aware UTC

@dataclass(frozen=True)
class PlanHints:
    playbook: str None = None
    servers: tuple[str, ...] = ()
    env: str None = None
    since: datetime None = None              # UTC
    until: datetime None = None              # UTC
    source: str = "rules"                      # "rules" | "bitnet"

class PlanAssistant(Protocol):
    async def hints(self, context: PlanContext) -> PlanHints None: ...

@dataclass(frozen=True)
class Condensed:
    text: str
    kept_lines: int
    total_lines: int
    source: str                                # "rules" | "bitnet"

class ResultCondenser(Protocol):
    async def condense(self, text: str, task: str None) -> Condensed None: ...

class DraftSummarizer(Protocol):
    async def summarize(self, question: str, findings: list[dict[str, Any]]) -> str None: ...
```

`HubRuntime` gets three attributes, all defaulting to `None`: `plan_assistant`, `condenser`, `full_results`. `JobRunner.__init__` gets the keyword-only arguments `plan_assistant=None`, `condenser=None`, `summarizer=None`. Track D's `bitnet.install()` sets them.

### 7.4 Settings (`settings.py`, documented in `docs/settings.md`)

| Key Default Meaning |
|---|---|---|
| `bitnet.port` | 8766 llama-server port on 127.0.0.1 (minimum 1024, maximum 65535) |
| `bitnet.threads` | 0 CPU threads; 0 = auto (`os.cpu_count() // 2`, at least 1) |
| `bitnet.model` | `BitNet-b1.58-2B-4T` | model folder under `vendor/bitnet/models/` |
| `bitnet.maxRestarts` | 3 sidecar restarts before `unavailable` |
| `bitnet.planTimeoutSeconds` | 3.0 budget for `PlanAssistant.hints` |
| `bitnet.condenseTimeoutSeconds` | 8.0 budget for `ResultCondenser.condense` |
| `bitnet.condenseMinChars` | 6000 minimum result text length to condense |
| `bitnet.fullResultTtlSeconds` | 1800 lifetime of a stored original |
| `bitnet.fullResultMaxEntries` | 50 originals kept in RAM (LRU) |
| `jobs.maxWindowHours` | 168 longest `since`→`until` span |

### 7.5 Audit and job events (never content)

| Event Where Fields |
|---|---|---|
| `hub.plan.created` (existing) | audit add `assist`: `off` \| `rules` \| `bitnet` \| `fallback` |
| `tool.result.condensed` (new) | audit | `request_id`, `server`, `tool`, `kept_lines`, `total_lines`, `source` (`rules` \| `bitnet` \| `fallback`), `duration_ms` |
| `bitnet.sidecar.state` (new) | audit | `state` (`starting` \| `ready` \| `restarting` \| `unavailable` \| `stopped`), `restarts`, `error_type` |
| `job.planned` (new) | job event (`store.append_event`) | `tool` = source (`rules` \| `bitnet` \| `fallback` \| `off`) |

Document every new audit event in `docs/security-and-logging.md` and `docs/reference.md` in the same task, or `check_docs.py` fails.

## 8. Execution rules for implementing agents

1. **Read the minimum:** this file, your track file, and the source files that track file names. Use `Grep` for symbols instead of reading whole large files.
2. **Stay in your files.** Change only the files your track file lists under "Owns". If you need anything else, stop. Add a line under "Blocked" in your track file, then tell the user.
3. **Branches and worktrees:**
   - Track 0: `git switch -c feat/bitnet-foundation master`.
   - Tracks A, B, C: after the user merges track 0, each runs `git worktree add ../mcp-hub-<a|b|c> -b feat/bitnet-<planning|condense|runtime> master`.
   - Track D: `feat/bitnet-integrate`, created from `master` after A, B and C are merged.
4. **Per task, in order:**
   1. Write the tests first, from the task's "Tests" list.
   2. Write the code until they pass.
   3. Run `uv run --no-sync python scripts/package_qc.py`. Also run `uv run --no-sync python scripts/smoke.py` when the task touches `server.py`, `service.py` or `preflight.py`.
   4. If both pass, commit on your track branch as `feat(bitnet): T0xx <summary>` with your own agent attribution line.
   5. Tick the checkbox and add a line to the Progress log of your track file.
5. **Follow AGENTS.md in full:**
   - English only; functions at most 50 lines.
   - No blocking work on the event loop.
   - Tunables go in settings; docs change with the code.
   - Tests run offline: mock `httpx`, subprocesses and the clock. Patch `config.PRIVATE_ROOT`; give `AuditLog` a temporary path and close it.
   - Never open the private files listed in AGENTS.md. Never read anything under `vendor/`.
6. **Do not change frozen contracts** (section 7.3) after track 0 is merged. If a contract is wrong, record it under "Blocked".
7. **Done for a track:**
   - all its tasks are ticked;
   - its acceptance checks pass;
   - the branch is pushed only if the user asks; otherwise it stays local for the user to merge.

## 9. Final verification (run by track D)

- `uv run python scripts/package_qc.py` and `uv run python scripts/smoke.py`.
- Manual checks:
  1. Switch BitNet on in the desktop app. Status goes `installing` → `ready`.
  2. Call `hub__plan` with `task` "check production hom nay loi sql" plus `project`. The summary shows servers including `mcp-sqlserver`, env `prod`, and a 24 h window. `logs/mcp-hub.jsonl` has `assist=rules`.
  3. Run a large log query. The result is condensed and has a footer. `hub__get_full_result` pages through the original.
  4. Kill `llama-server`. Steps 2 and 3 still work, with `assist`/`source` = `fallback` or `rules`.
  5. Turn off cloud keys for a test project (or point to an empty agents config in a temp private dir). The job returns `Draft (local model)`.
  6. Switch BitNet off. Outputs equal today's.

## 10. Analysis (Spec Kit analyze)

| Requirement Tasks |
|---|---|
| FR-001 T003, T005, T006, T009 (regression tests), T401 |
| FR-002 T101, T102 |
| FR-003 T006 (`merge_plan_arguments`), T009 |
| FR-004 T005, T006, T009, T102, T202 |
| FR-005 T006, T007 |
| FR-006 T006, T007 |
| FR-007 T005 |
| FR-008 T201, T202 |
| FR-009 T004 |
| FR-010 T008 |
| FR-011 T302 |
| FR-012 T301, T401 |
| FR-013 T005, T006, T009, T301 (tests assert no content in audit lines) |
| FR-014 T303, T304 |
| US4 T402 |
| SC-001…005 every task; T404 |

**Risks:**
- The bitnet.cpp `llama-server` may not support JSON mode or `response_format`, so `client.py` parses leniently (first `{…}` block) and validates strictly.
- Building on Windows needs VS 2022 with the ClangCL toolset and cmake ≥ 3.22, so the installer checks and reports instead of failing the Hub.
- With a 4096-token context, the condenser chunks input to about 2,500 tokens (≈ 10,000 characters) per request.
- CPU speed varies, so budgets are settings and fallbacks are mandatory.

**Open questions:** none.

## 11. Progress log

- 2026-11-08 Claude (Opus): plan agreed with the user and written. Replaces the earlier draft `2026-11-08-bitnet-job-router.md`. Next: an agent implements track 0.

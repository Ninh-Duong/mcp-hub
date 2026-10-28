# Shared Agent Hub Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `executing-plans` to implement this plan task-by-task. Do not edit the three existing child repositories unless integration proves a documented tool contract is insufficient. The source-reader child is a new sibling repository.

**Goal:** Convert `mcp-hub` into one authenticated, local Windows service shared by AI clients, with an asynchronous issue-investigation team and dashboard.

**Architecture:** Keep the existing MCP proxy and child tools. Add Streamable HTTP, a local stdio bridge, a bounded durable job runner, role-based provider agents, a read-only source child, and a localhost dashboard. Use Windows Task Scheduler and DPAPI for the first supported service lifecycle.

**Tech Stack:** Existing Python 3.12 Hub, MCP Python SDK, asyncio/AnyIO, SQLite, Windows Task Scheduler/DPAPI via standard-library `ctypes`, plain browser HTML/CSS/JavaScript, official OpenAI/Anthropic/Gemini SDKs. New source child: Python 3.12, MCP Python SDK, standard library for repository traversal/search.

**Spec:** `docs/superpowers/specs/2026-10-28-shared-agent-hub-design.md`

**Implementation status:** Code and operator documentation are in place across `mcp-hub` and the new sibling `mcp-read-source`. The existing logs, Jira, and PR child repositories are unchanged. Unit tests pass; see [Verification and handoff](#verification-and-handoff) for the one environment-dependent smoke failure and the remaining manual acceptance.

## Global Constraints

- Full service lifecycle support is Windows-first; keep stdio useful for development and legacy clients.
- Bind network listeners only to `127.0.0.1` and require the local bearer token for MCP and dashboard control endpoints.
- Support one signed-in user; all clients for that user share jobs.
- Maximum 2 active jobs, 15 minutes per job, and 30 child-tool calls per job.
- Encrypt detailed job results with Windows DPAPI and retain them for 30 days.
- Never commit credentials, machine-specific paths, private configurations, tool results, or model input/output logs.
- Keep all existing direct child tool names, schemas, and side-effect annotations stable.
- Existing child MCP code is out of scope unless a proven contract gap blocks the acceptance criteria.

---

### Task 1: Add the shared local service and transports

**Files:**
- Modify: `src/mcp_hub/server.py`
- Create: `src/mcp_hub/service.py`, `src/mcp_hub/bridge.py`, `scripts/hubctl.ps1`
- Test: `tests/test_stdio.py`, new `tests/test_http.py`, new `tests/test_bridge.py`
- Modify: `config/hub.example.json`, `AGENTS.md`

**Interfaces:**
- `service.run_http(host: str, port: int)` runs the existing Hub runtime with one child connection set and exposes Streamable HTTP plus dashboard routes.
- `bridge.main()` exposes a stdio MCP endpoint to a client and forwards protocol messages to the authenticated local HTTP endpoint.
- `hubctl.ps1` supports `install`, `status`, `start`, `stop`, `restart`, and `open` using a per-user scheduled task.
- Generate/load a local bearer token from ignored service configuration. Never print or log its value.

- [x] **Step 1: Add transport tests first.** Extend the existing fake-child stdio test to assert the existing stdio startup still exposes `hub__status` and the same child alias. Add HTTP initialization/list-tools tests against a temporary local port, plus invalid-token rejection.
- [x] **Step 2: Run focused tests and confirm the new cases fail** because HTTP host, bridge, and token middleware do not exist.
- [x] **Step 3: Refactor startup behind one runtime lifecycle.** Extract the `HubRuntime` connection lifetime so stdio and HTTP entry points call the same initializer and tool handlers. HTTP must use the MCP SDK `Server.streamable_http_app()` entry point already present in the installed SDK; mount dashboard routes on the same Starlette app.
- [x] **Step 4: Implement localhost auth.** Require bearer token on MCP requests and all dashboard API routes. Reject non-loopback binds, invalid Host, invalid Origin, and missing/incorrect tokens. Expose only a minimal unauthenticated `/health` response with no child/job details.
- [x] **Step 5: Implement the stdio bridge.** Read the token from private service configuration inside the bridge process, connect with the SDK HTTP client, and forward protocol requests without passing token material to tool output or model content.
- [x] **Step 6: Implement `hubctl.ps1`.** Register/start/stop the user logon task, report task and `/health` status, perform restart through a hidden detached helper, and open the dashboard using a short-lived pairing token. Do not request administrator privileges.
- [x] **Step 7: Run focused transport tests.** Confirm two HTTP clients can list tools concurrently; exercise stdio, HTTP, bridge, wrong token, wrong origin, and shutdown cleanup.
- [x] **Step 8: Update startup and client docs** with HTTP URL, token handling, bridge entry, and how to install/status/stop/start/restart the scheduled task.

### Task 2: Add private agent configuration and provider adapters

**Files:**
- Create: `config/agents.example.json`, `src/mcp_hub/agents/config.py`, `src/mcp_hub/agents/providers.py`
- Modify: `pyproject.toml`, `AGENTS.md`, `scripts/private_config.py`
- Test: new `tests/test_agent_config.py`, `tests/test_model_providers.py`

**Interfaces:**
- `AgentConfig(name, provider, model, api_key)` is loaded only in process memory; `repr` must omit the key.
- `ModelProvider.complete(messages, tools, limits) -> ModelTurn` normalizes provider text and tool-call requests.
- Implement providers `openai`, `anthropic`, and `gemini`; use provider-native tool-call APIs, not a single compatibility API.
- `private_config.py agent-status` reports agent/provider/model/configured state only. It never returns key values. The user edits ignored local agent configuration manually.

- [x] **Step 1: Write config tests.** Cover all three provider identifiers, missing API key, missing model, invalid provider, unknown agent, and key omission from repr/errors.
- [x] **Step 2: Write adapter tests with mocked SDK responses.** Cover text-only final responses, one tool call, multiple tool calls, malformed arguments, API timeout, rate limit, and provider errors without making network calls.
- [x] **Step 3: Run the focused tests and verify failure** for missing config loader/adapters.
- [x] **Step 4: Add the ignored private config flow.** Add an example with `<SET_BY_USER>` placeholders and one config record per role: coordinator, logs, source, Jira, PR. Update `AGENTS.md` so future agents may read the example but never open/print the private real config. Extend the private config helper to report key presence only.
- [x] **Step 5: Add provider adapters** with explicit per-provider request/response normalization. Validate every returned tool name against the role allowlist and validate parsed JSON arguments against the MCP tool schema before execution.
- [x] **Step 6: Add only required provider SDK dependencies** and update the lockfile. Keep network clients reusable for the service lifetime; configure explicit timeouts.
- [x] **Step 7: Run config and provider tests.** Assert mock calls receive the expected model, tool schema, and key in the authorization field, but ensure test output/errors never contain the key.
- [x] **Step 8: Document models and providers** without suggesting a specific model name or embedding any real token.

### Task 3: Build bounded jobs, side-effect gates, and encrypted storage

**Files:**
- Create: `src/mcp_hub/jobs/store.py`, `src/mcp_hub/jobs/runner.py`, `src/mcp_hub/jobs/policy.py`
- Modify: `src/mcp_hub/server.py`, `src/mcp_hub/audit.py`, `docs/child-mcp-compatibility.md`, `docs/client-rules.md`
- Test: new `tests/test_jobs.py`, `tests/test_job_store.py`, `tests/test_side_effect_policy.py`

**Interfaces:**
- Store methods: `create_job`, `append_event`, `save_encrypted_result`, `get_job`, `get_detail_page`, `approve_pending_call`, `cancel_job`, `purge_expired`.
- Runner states: those enumerated in the approved spec, including `awaiting_input`; every transition emits a metadata-only event.
- Side-effect policy distinguishes automatic encrypted cache writes from explicit/remote writes requiring approval.

- [x] **Step 1: Test state transitions.** Cover submit, queued-to-running, awaiting input, partial/completed, failed, cancelled, interrupted on restart, and invalid transition rejection.
- [x] **Step 2: Test encrypted storage.** Use a mocked DPAPI boundary in unit tests; verify stored result bytes are not plaintext, detail paging decrypts only inside the service, expired jobs are purged, and service restart marks running jobs interrupted.
- [x] **Step 3: Test approval gates.** Confirm an explicit write is never dispatched before dashboard approval or a matching one-time code; reject wrong, expired, and reused codes; confirm documented automatic caches proceed.
- [x] **Step 4: Run focused tests and confirm they fail** before implementation.
- [x] **Step 5: Implement the local store.** Use SQLite for metadata/events. Encrypt full tool results and detailed summaries with DPAPI before inserting blobs. Keep job IDs, timestamps, state, and agent role as the only unencrypted indexed fields. Purge details at 30 days on startup and every 24 hours.
- [x] **Step 6: Implement bounded async execution.** Enforce at most two active jobs, a 15-minute deadline, and a 30-tool-call budget. Do not automatically replay tool calls after process restart.
- [x] **Step 7: Implement approval interception.** Before a gated call, store its exact tool name and encrypted arguments, transition to `awaiting_approval`, and pause. Bind the one-time code to the job and pending-call hash; approve only that call.
- [x] **Step 8: Add MCP job tools**, including `hub__provide_job_input` for validated environment input, and metadata-only audit events. Never log tool arguments, tool results, provider prompts/completions, keys, or approval codes.
- [x] **Step 9: Update side-effect guidance** to state that documented encrypted local caches may be written during reads, while explicit writes and external writes require user approval.
- [x] **Step 10: Run focused job/storage/policy tests** and inspect audit output for metadata-only behavior.

### Task 4: Create the `mcp-read-source` child repository

**Files:**
- Create in new sibling repo: `pyproject.toml`, `src/mcp_read_source/server.py`, config loader, tests, `AGENTS.md`, `README.md`
- Modify in Hub: `config/mcps/mcp-read-source.example.json`, `config/hub.example.json`, `docs/mcps/mcp-read-source.md`, `scripts/smoke.py`, `tests/test_smoke.py`

**Interfaces:**
- `source__search(project, query, glob?, limit?) -> {matches, total}`
- `source__read(project, path, start_line?, max_lines?) -> {project, path, start_line, end_line, total_lines, content}`
- Health check `source__check_connection` returns configured project aliases only, never machine paths.

- [x] **Step 1: Create a separate Python 3.12 sibling repository** and define config with project aliases and local roots. Keep real paths in ignored local config only.
- [x] **Step 2: Write source safety tests first.** Cover allowed tracked and untracked source files, traversal, symlink escape, ignored `.git`/vendor/build directories, `.env`/private files, binary file, file over 1 MiB, more than 500 candidate files, and read request over 200 lines.
- [x] **Step 3: Run the focused tests and confirm failures** before adding the tools.
- [x] **Step 4: Implement project-root validation** after resolving aliases and symlinks. Reject every path whose resolved target is outside the configured root.
- [x] **Step 5: Implement bounded search/read** using safe argument arrays and text files only; do not execute repository code or write files.
- [x] **Step 6: Add a read-only health tool and child contract.** Document inputs, output shapes, filters, limits, privacy behavior, and diagnostics.
- [x] **Step 7: Register the source child in Hub templates and smoke checks.** Keep it disabled/unavailable until local project roots are configured; do not place absolute paths in tracked files.
- [x] **Step 8: Run child tests and Hub integration tests** with temporary repos and no upstream service.

### Task 5: Add the coordinator and specialist workflows

**Files:**
- Create: `src/mcp_hub/agents/coordinator.py`, `src/mcp_hub/agents/specialists.py`, `docs/agents/coordinator.md`, `docs/agents/logs.md`, `docs/agents/source.md`, `docs/agents/jira.md`, `docs/agents/pr.md`
- Modify: `src/mcp_hub/jobs/runner.py`, `src/mcp_hub/server.py`
- Test: new `tests/test_issue_workflow.py`, `tests/test_agent_permissions.py`

**Interfaces:**
- Coordinator accepts a validated `IssueRequest(project, question, env?, issue_key?, rcid?)` and produces specialist task requests plus a user-facing synthesis.
- Each specialist receives a task, project/env identifiers, and only its exact child tool allowlist. It returns `{summary, evidence_refs, limitations}`.
- Evidence references identify source record/tool and line or ticket section; full result body remains encrypted in the job store.

- [x] **Step 1: Write workflow tests** for a ticket-vs-logs mismatch, missing env, missing child, missing key, conflicting evidence, and a specialist failure that should produce a partial result.
- [x] **Step 2: Write permission tests** proving logs agent cannot call Jira/PR/source tools, source agent cannot access an unregistered repo, and coordinator cannot invoke child tools directly.
- [x] **Step 3: Run focused tests and confirm failure** before implementing coordinator/specialists.
- [x] **Step 4: Implement fixed specialist roles.** Use role docs in prompts, call only provider-configured agents, and validate requested actions against the specialist's allowlist.
- [x] **Step 5: Implement issue investigation flow.** Check project/environment first, gather Jira/source/log/PR evidence as relevant, permit independent specialist work to run concurrently, then synthesize a short conclusion with citations and uncertainty.
- [x] **Step 6: Apply data minimization before provider calls.** Build model context from only selected result fields and bounded excerpts; redact credential patterns and sensitive fields. Keep unfiltered result encrypted locally.
- [x] **Step 7: Exercise the workflow with fake provider adapters and fake child MCPs.** Confirm the job returns immediately, status advances, evidence detail is retrievable, and no sensitive content appears in audit events.

### Task 6: Build the dashboard and service controls

**Files:**
- Create: `src/mcp_hub/dashboard/index.html`, `src/mcp_hub/dashboard/app.js`, `src/mcp_hub/dashboard/style.css`, `src/mcp_hub/dashboard/api.py`
- Modify: `src/mcp_hub/service.py`, `scripts/hubctl.ps1`
- Test: new `tests/test_dashboard_api.py`, `tests/test_service_control.py`

**Interfaces:**
- Dashboard API exposes health, child/agent readiness, list/detail jobs, timeline pagination, approve, cancel, and restart.
- The browser displays job state, agent/provider/model, tool action/timing, evidence reference, short summary, and detail-on-demand. It does not expose chain-of-thought or API keys.

- [x] **Step 1: Write API tests** for auth, health, job list/detail, pagination, approval, cancellation, restart request, and hidden key values.
- [x] **Step 2: Run dashboard API tests and confirm failure.**
- [x] **Step 3: Implement a plain HTML dashboard** served from the Hub; poll status every two seconds while a job is active and every ten seconds otherwise. Add accessible status text and keyboard-operable buttons.
- [x] **Step 4: Implement controls.** Dashboard stop/restart must invoke a hidden detached `hubctl` helper so the running server can shut itself down safely. Starting from a stopped state remains available through `hubctl start` and `hubctl open`.
- [ ] **Step 5: Verify localhost auth, shared visibility, detail-on-demand, agent-job approval, and restart** using two browser sessions and MCP clients. Confirm existing direct child tools retain their current behavior. (manual two-client and Windows Task Scheduler acceptance remains pending; see Verification and handoff).

### Task 7: Document client migration, operator process, and release checks

**Files:**
- Modify: `README.md`, `docs/architecture.md`, `docs/security-and-logging.md`, `docs/connecting-a-client.md`, relevant `docs/clients/*.md`, `AGENTS.md`
- Create: `docs/operations/shared-agent-hub.md`

- [x] **Step 1: Write operator runbook** for install scheduled task, configure projects/providers, start/stop/status/restart, open dashboard, inspect a job, approve a write, recover after reboot, and troubleshoot service/auth/provider/child failures.
- [x] **Step 2: Update each supported client guide** with the HTTP URL + bearer-header entry where verified; otherwise give the stdio bridge entry. Preserve old stdio instructions as compatibility path.
- [x] **Step 3: Document migration** from one spawned Hub per client to the shared task. Tell the user that client configuration files are user-level settings and are not changed by repository code.
- [x] **Step 4: Review tracked docs for machine-specific paths and secret examples.** Use placeholders and relative paths only.
- [ ] **Step 5: Run the repo definition of done before every commit:** `uv run python -m unittest discover tests`, `uv run python scripts/smoke.py`, and `uv run python scripts/check_leaks.py`. Run the new source repo's own required test/check commands too. (the OpenObserve STG probe is blocked by missing local credentials; see Verification and handoff).
- [ ] **Step 6: Manual acceptance:** install on Windows, restart machine, verify automatic Hub startup, connect two AI clients, run one issue job, retrieve detail, approve a gated write, stop/restart, and verify job/result expiry behavior with a shortened test clock. (requires user configuration and live Windows/client acceptance).


## Verification and handoff

Automated and integration checks on 2026-10-28:

- Hub unit suite: `uv run --locked python -m unittest discover tests` — 105 tests passed.
- Source child suite: 6 passed and 1 skipped (7 total); the external symlink-escape test was skipped because symlink creation is unavailable in this Windows environment. A platform-independent regression test for a safe-looking in-root alias to `.env` passed.
- PowerShell parser: `scripts/hubctl.ps1` and `scripts/http_headers.ps1` parsed successfully.
- Hub leak check: passed in the final validation run.
- Hub smoke: source reader (3 tools), Jira (5 tools), and PR Companion (10 tools) passed. Logs (12 tools) passed for `dev` and `prod`; `stg` failed its read-only `logs__list_openobserve_organizations` check because local OpenObserve STG credentials are not configured. Redacted Hub audit evidence: request ID `5`, server `mcp-server-logs`, tool `list_openobserve_organizations`, summary `Missing OPENOBSERVE_EMAIL, OPENOBSERVE_TOKEN (STG)`.

Code-review follow-up: search filters both the requested path and its resolved in-root target before reading; specialist and coordinator prompts pass through the provider redaction pipeline, with configured provider keys registered for exact-value redaction; and service stop uses an authenticated shutdown request, a 20-second Uvicorn drain deadline, and a 45-second `hubctl` wait so the Hub can cancel slow requests and close jobs/child connections before its scheduled task exits. Unit tests cover these changes. Windows Task Scheduler, two-client, approval, and end-to-end restart acceptance still require a configured local setup.

The source child starts and completes its MCP handshake after adding the `python -m` entry point. Before production use, configure the private provider/project files described in the runbook, run `uv run python scripts/check_leaks.py`, rerun smoke after configuring STG if that environment is expected, then run `hubctl install` and complete the two-client Windows acceptance. This change has not installed a scheduled task, edited user-level AI client settings, or committed files.

## Execution order

Complete Tasks 1–3 in the Hub first. Task 4 can begin once the source-tool contract is accepted and must be ready before Task 5 integration. Complete Task 5 before dashboard detail views (Task 6). Finish with Task 7 and the full acceptance run. Keep the existing logs/Jira/PR child repositories unchanged unless an observed contract gap blocks a stated acceptance criterion; if that occurs, stop and add a targeted child-repo task with its own instructions and checks.

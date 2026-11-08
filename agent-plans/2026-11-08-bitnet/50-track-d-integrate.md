# Track D: wiring, offline draft, client docs, final checks

Status: approved · Branch: `feat/bitnet-integrate` (from `master` after tracks A, B and C are merged) · Runs last, alone · Read [00-overview.md](00-overview.md) first.

Goal: connect the parts at start, add the offline draft, document the feature for operators and clients, and run the final verification (overview section 9).

## Owns

- `src/mcp_hub/bitnet/__init__.py` (replace the skeleton), `src/mcp_hub/bitnet/summarize.py` (new)
- `src/mcp_hub/service.py`
- `src/mcp_hub/jobs/runner.py` (the offline branch in `_summarize` / `_investigate` only)
- `docs/bitnet.md` (new), `docs/client-rules.md`, `docs/architecture.md`, `README.md` (one line), `AGENTS.md` (Start-here row)
- the server instructions text in `src/mcp_hub/server.py` (`_server_instructions` only)
- the matching tests

## Tasks

### [ ] T401 `install()` and service wiring
- `bitnet/__init__.py`:
  ```python
  @dataclass
  class BitnetExtension:
      sidecar: Sidecar
      def dashboard_extension(self) -> DashboardExtension: ...   # status_fields -> sidecar.status_fields()
      async def close(self) -> None: ...                         # await sidecar.stop()

  def install(runtime: HubRuntime, job_runner: JobRunner, *, root: Path, audit: AuditLog) -> BitnetExtension:
      settings = runtime.settings.bitnet
      client = BitnetClient(settings.port)
      runtime.plan_assistant = BitnetPlanAssistant(client, timeout=settings.plan_timeout_seconds)
      runtime.condenser = BitnetCondenser(client, max_chars=settings.condense_min_chars, timeout=settings.condense_timeout_seconds)
      runtime.full_results = FullResultStore(settings.full_result_max_entries, settings.full_result_ttl_seconds)
      job_runner.plan_assistant, job_runner.condenser = runtime.plan_assistant, runtime.condenser
      job_runner.summarizer = BitnetDraftSummarizer(client)
      sidecar = Sidecar(root, settings, audit, log_dir=runtime.child_log_dir)
      return BitnetExtension(sidecar)
  ```
  - The assistant and condenser already fall back on their own when the sidecar is not ready, because the client returns `None`. No readiness gating is needed.
  - The condenser's step A works without BitNet, which matches FR-004.
- `service.py`:
  - `install_extensions` also handles `BITNET` with a lazy `from .bitnet import install as install_bitnet`.
  - Return the installed extensions as a small list or dict instead of a single `ui`. Refactor the minimum: `_serve` iterates over them for `start`, `close` and `dashboard_extension`.
  - `hub.started` `extensions=` lists every installed name.
  - The sidecar starts after `_serve` begins and stops after `job_runner.close()`.
- Tests (`tests/test_service.py`, everything faked):
  - requesting `bitnet` installs it and sets the runtime hooks;
  - not requesting it leaves the hooks `None`;
  - `activity-ui` and `bitnet` together both install;
  - close order: runner, then extensions.

### [ ] T402 Offline draft (US4)
- `bitnet/summarize.py`:
  ```python
  class BitnetDraftSummarizer:  # implements extensions.DraftSummarizer
      async def summarize(self, question: str, findings: list[dict[str, Any]]) -> str None:
          # prompt: "Summarize these findings for an operator in at most 8 bullet points. Cite [source_id]. Do not invent."
          # user text <= 10,000 chars (truncate findings); client.complete_text(max_tokens=400, timeout=30)
          # return "Draft (local model):\n" + text, or None
  ```
- `jobs/runner.py`:
  - In `_investigate`, roles with no provider use the deterministic path when `self.summarizer` is set. For each such role, run the playbook's read-only tools with fixed arguments. Keep the mapping small and explicit, in a module constant:
    - logs: `logs__get_openobserve_service_logs` or `logs__search_openobserve_logs`, using the project's log target and the window, only when the project has log targets;
    - sql: `sql__search_context` with the question.
  - Run them through `_call_child_tool`, so the policy, the approvals and the evidence storage stay the same. Condense each result and build a `SpecialistResult` with the evidence refs and the summary `""`.
  - Read the tool input schemas from `runtime.public_tools`, and pass only the arguments the schema declares.
  - In `_summarize`, when `coordinator is None and self.summarizer is not None`, call the summarizer with `_findings_payload`-style dicts. On `None`, fall back to today's `_fallback_summary`. A draft counts as `synthesized=False`, so the final state stays `partial`.
- Tests:
  - no providers + summarizer → the fixed tools are called, a draft summary is saved, and the state is `partial`;
  - no summarizer → today's behavior (regression);
  - a summarizer that returns `None` → the fallback summary.

### [ ] T403 Docs
- New `docs/bitnet.md` (the owning doc). It covers:
  - what the extension does (plan, condense, offline draft);
  - the switch;
  - requirements (VS 2022 Build Tools with Clang, cmake ≥ 3.22, about 1.5 GB disk, 2–3 GB RAM while running);
  - the install path `vendor/bitnet/`;
  - the settings, linking to `docs/settings.md`;
  - the audit events, linking to `docs/security-and-logging.md`;
  - the fallbacks;
  - how to remove it: switch it off, then delete `vendor/bitnet/`.
- `docs/client-rules.md`:
  - Bump `Version:` to the implementation date.
  - Add a short section: "Results may be condensed. A footer names a `result_id`; call `hub__get_full_result` only when the evidence you need is missing from the condensed text. With BitNet on, `hub__plan` may fill playbook, servers, env, and the time window from `task` and `project`; read the returned plan and use its window."
  - Keep the file free of machine paths and relative links, as its header says.
- Update `_server_instructions` in `server.py` with the same two sentences in short form, and its test.
- `docs/architecture.md`: the planner, condenser and summarizer hooks, and the rule that core never imports `bitnet`.
- `README.md`: a one-line mention with a link to `docs/bitnet.md`.
- `AGENTS.md`: add the Start-here row `| Turn the local BitNet AI on or off, or change it The user switches it in the desktop app; see [docs/bitnet.md](docs/bitnet.md). |`
- `check_docs.py` must pass.

### [ ] T404 Final verification and converge
- Run overview section 9 in full and record each manual result as one line in this file's Progress log.
- Converge: walk the table in overview section 10. For every FR, US and SC, confirm that a test or a manual check covers it. Add any gap as a new unchecked task here, implement it, and re-run QC.
- Set the overview `Status: done` only after the user confirms the manual checks.

## Acceptance for track D
- Everything in overview section 9 passes.
- With the extension off, `hub.started` shows `extensions=[]` and all regression tests pass.

## Blocked

- (none)

## Progress log

- 2026-11-08 Claude (Opus): track written. Next: wait for A, B, C to merge, then T401.

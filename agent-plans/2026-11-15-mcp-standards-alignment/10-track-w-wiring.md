# Track W: wiring (Phase 2)

Status: waiting for Phase 1 (tracks A to F merged into `arch/base`)
Branch: `arch/track-w` (from `arch/base` after all Phase 1 merges). Worktree: `../mcp-hub-track-w`.

This is the only track that edits `server.py`. Tasks run one at a time, one commit each, in the order below.

Owns:
- `src/mcp_hub/server.py`, `src/mcp_hub/service.py`, `src/mcp_hub/batch.py`
- `src/mcp_hub/activity_ui/extension.py`, `src/mcp_hub/activity_ui/client_sessions.py`
- `src/mcp_hub/bitnet/__init__.py` (only the `FullResultStore(max_bytes=...)` call)
- `tests/test_server*.py`, `tests/test_batch.py`, `tests/test_activity*.py` (additions)
- `docs/architecture.md`, `docs/child-mcp-compatibility.md`, `docs/batch.md`, `docs/usage-policy.md`, `docs/operations/shared-agent-hub.md`, `AGENTS.md` (the dependency line only)

Read first: `src/mcp_hub/server.py` (`_maybe_condense`, `_finish_condensed`, `_pack_batch_items`, `_get_full_result`, `_register_routes`, `call_child_tool`, `_invoke_child`, `create_mcp_server`, `_ERROR_PREFIXES` near line 58), and the Phase 1 modules: `result_shape.py`, `events.py`, `schema_rewrite.py`, `tool_pins.py`, `instructions.py`, `full_results.py`, and the new policy and cache behaviour.

## Carried forward from Phase 1 (read before starting)

These came out of the Phase 1 reports. Each one is a requirement on this track.

1. `FullResultStore.put` returns `str | None`. It returns `None` when one entry is larger than `max_bytes`. The caller in `_maybe_condense` (server.py, around line 556) must return the uncondensed result in that case. Test it.
2. Legacy delivery is unverified for Hub-side list changes. The SDK sends standalone notifications (no related request) to `_NoChannelOutbound` on a stdio legacy session, so they are dropped. T73 must test delivery per protocol era, over stdio and HTTP. If a legacy client cannot receive them, do not claim it; document the limitation in `docs/child-mcp-compatibility.md` and record it in the progress log.
3. Progress reaches the child only after T76 is in place. Track C's bridge side is done (it forwards `progress_token` and relays progress), but the Hub emits none until `_call_child` passes `progress_callback`.
4. `PinStore.load()` runs before the first `check()`. `save()` runs through `asyncio.to_thread`. Pass tools in the same stable order used for `tools/list`.
5. `ChildSupervisor.tools` is a read-only property (Track B). The `on_tools_changed(server_id)` callback reads `supervisor.tools` to see the relisted tools.
6. `ChildOpener` is a `typing.Protocol` whose call accepts a keyword-only `message_handler`. Custom openers must accept it. server.py uses `ChildOpener` only as an annotation, so only pass the new handler where the supervisor builds its client.
7. A `$ref` root whose target definition has `additionalProperties: false` still refuses `hub_plan_id` (Track D). This is a known gap; record it in `docs/usage-policy.md` or handle it in `with_plan_id` with a test. Do not leave it silent.
8. Wire the repeat window: `PlanBook` needs `repeat_after_seconds` from `Settings().policy.repeat_after_seconds` (T44 default is 0, so behaviour is unchanged until set). Add the `policy:repeat` window line to `docs/usage-policy.md` (around line 67).
9. Prompt caching (Track F, T64) is verified offline only. A specialist prefix may fall below the model's minimum cacheable size, in which case it caches silently. Do not claim savings in docs. Record that a live run must check `cache_read_input_tokens`; that check belongs to Track Q or a manual smoke run.
10. `build_instructions` (Track F, T60) is not yet used by server.py. T80 wires it.
11. Result text with no evidence ID (validation error, child exception) clears to a note with an empty `source_id` (Track F). Keep that behaviour in any server.py wiring.
12. The bridge stops its presence stream when the upstream Hub negotiates the 2025 protocol, and logs one line. Hub list changes are then not relayed on that connection (Track C, card behaviour). Confirm the bridge's client negotiates the 2026 protocol in the wiring tests; if it cannot, record the limitation in `docs/architecture.md`.
13. `docs/architecture.md` must name `bridge_proxy.py`, the instructions pass-through, the progress relay, and the bridge's own process-local `subscriptions/listen` bus. Track C asked for this; it is part of T081.
14. One unit-test run on an earlier merge printed an SDK trace, `Error parsing SSE message` (anyio `BrokenResourceError`), while every test passed. It did not reproduce on the full merge. If it appears again, find the teardown that causes it before adding any suppression.

## Tasks

- [x] T070 `_maybe_condense` calls `result_shape.is_condensable(tool_output_schema, result)` first and returns the result untouched when it is False. `_finish_condensed` builds its result with `with_condensed_meta`. The batch packing applies the same rule. Test: a tool with `outputSchema` returns a result that validates against that schema with `jsonschema`, including after a large text result.
- [x] T071 `hub__get_full_result` uses `parse_page_args` and returns `isError=true` with the error text for bad input (FR-003). Test: `limit` set to a list gives an error result, not an exception.
- [ ] T072 `create_mcp_server` installs `runtime.events.listen_handler` on the server (the `subscriptions/listen` handler). `activity_ui/extension.py` wraps that handler for its presence feed instead of registering its own. Test: with the activity UI on and off, `subscriptions/listen` answers, and only one handler is registered.
- [x] T073 `_register_routes` computes a signature from `tool_pins.fingerprint` over every exposed tool (sorted by server id then tool name) and calls `events.tools_changed(signature)`. The first build calls `events.prime(signature)` instead. Audit `tools.list_changed.sent` (constant from `audit_events`) when a publish is scheduled. Test: a fake child that reconnects with one new tool yields exactly one notification (SC-003).
- [ ] T074 The supervisor callback from Track B (`on_tools_changed(server_id)`) is passed when children are created; the callback relists routes through `_register_routes`. Test: a child-sent change updates the tool list.
- [ ] T075 `tools/list` order is fixed: Hub tools, then each server in config order, then job tools; each server's tools sorted by name. The cache hint comes from `Settings().protocol.tools_list_ttl_ms`, applied through `mcp.server.caching.apply_cache_hint` or `cache_hints` (verify the SDK argument). Test: two builds give the same order.
- [ ] T076 `_call_child` passes `progress_callback` to `client.call_tool` when the downstream request has a `progressToken`, and forwards `_meta` minus connection-owned keys (FR-010). Test: a fake child that reports progress causes downstream progress notifications.
- [ ] T077 `connect` waits at most `children.startup_wait_seconds` for the first attempts; children that answer later are registered when they connect (SC-004). Test: a fake child that hangs does not delay the Hub past 12 seconds in the test's clock or timeout settings.
- [ ] T078 `PinStore.check` runs inside `_register_routes`, through `asyncio.to_thread` for loading and saving. In warn mode, changed tools write `tool.definition.changed` (with `server` and `tool` only) and `hub__status` lists them under a `tool_pins` field. The pin file path is `config/private/tool_pins.json` under the private root. Test: a changed fake definition writes the event and does not hide the tool.
- [ ] T079 `validate_args` runs in `call_child_tool` when `protocol.validate_arguments` is `warn`. A failure writes `tool.arguments.invalid` with the field paths and the call still runs. Batch items get the same check. Test: a wrong-typed argument still reaches the fake child and writes the audit line.
- [ ] T080 `create_mcp_server` builds the server instructions with `instructions.build_instructions`. The summary is the existing short summary; sections are the tool list per server and then `client-rules.md`. Remove live connection counts from the instructions; `hub__status` still reports them. Test: the instructions are at most 4096 characters and start with the summary.
- [ ] T081 Pass `Settings().bitnet.full_result_max_bytes` to the `FullResultStore` constructor. Update `AGENTS.md` dependency line and `docs/architecture.md` (module table, failure handling, list_changed, condensing rules), `docs/child-mcp-compatibility.md` (`isError`, `outputSchema`, `list_changed`), `docs/batch.md`, `docs/usage-policy.md` (repeat and rate of plan ids, validation warn mode), `docs/operations/shared-agent-hub.md`.

Also wire the Phase 1 settings values: `policy.repeat_after_seconds` into the policy object, `children.startup_wait_seconds` into `connect`, `protocol.list_changed_debounce_seconds` into `HubEvents`, `security.tool_pin_mode` and `max_description_chars` into the pin check, and `jobs.*` into the job runner and specialists. Each value is passed when the object is created, never read from `hub.json` inside a module.

## Verify (after each task)

```
cd "../mcp-hub-track-w"
uv run --no-sync python -m unittest discover tests -q
uv run --no-sync python scripts/package_qc.py
```

At the end, run the Hub in this checkout with `MCP_HUB_PRIVATE_DIR`, `MCP_HUB_LOG_DIR`, and a free port set, and run `uv run --no-sync python scripts/smoke.py` with the same environment. Record the result, including any child that is `WARN` (a missing account is expected) or `FAIL` (report it, do not hide it).

## Stop and ask

- A Phase 1 module's signature differs from its card.
- Any existing test that asserts the old instructions, the old tool order, or the old condensed shape fails. Report the test names; do not rewrite them without a decision.
- The change would add a second place where a default value is written.

## Progress log

- (agent: add one line per task here)
- 2026-11-15 (track W, T070): done. _maybe_condense gates on result_shape.is_condensable with the child's outputSchema; the footer path adds _meta mcp-hub/condensed; batch packing uses the same gate. Carry-forward 1 done in the same commit: FullResultStore.put returning None returns the original result, and no tool.result.condensed line is written for it. Lossless compaction has no stored result, so it carries no _meta. Tests: tests/test_server_condense_shape.py.
- 2026-11-15 (track W, T071): done. hub__get_full_result reads offset and limit with parse_page_args; a bad value (list, text, zero, boolean) returns isError with the message and never raises. Tests: tests/test_server_full_result.py.
- 2026-11-15 (track W, T072): BLOCKED, not implemented. The card says create_mcp_server installs runtime.events.listen_handler for every Hub, and that the activity UI wraps it. Two existing tests assert the old behaviour, that a Hub without the activity UI does not serve subscriptions/listen: tests/test_extensions.py ActivityUiTest.test_attach_server_serves_presence_listen (assertIsNone before attach_server) and tests/test_bridge.py PresenceTest.test_presence_returns_against_a_hub_without_the_activity_ui (hold_presence then waits and times out, because the Hub now answers the listen). A trial change made both fail, and nothing else. The trial is reverted. Decision needed: change those two tests, or keep listen conditional. Tasks that do not need the listen handler continue (T073 onward).
- 2026-11-15 (track W, T073): done with deviations. HubRuntime.events = HubEvents(protocol.listChangedDebounceSeconds); connect primes the signature; _register_routes returns a signature over (server, tool, tool_pins.fingerprint) and child_connected announces it through announce_tools (tools_changed, then tools.list_changed.sent). Deviation 1: the audit line is written when a publish is scheduled (card wording), so a burst inside one window writes one line per signature change; docs/security-and-logging.md says one line per burst. Needs a decision (count at publish needs a hook in events.py, Track B). Deviation 2: client-level SC-003 and carry-forward 2 (delivery per protocol era over stdio and HTTP) cannot be checked yet: the listen handler is blocked (T072). The test checks the bus publish count only. Not verified: 2025 clients receive nothing from the bus, so Hub-side list changes reach only subscriptions/listen streams; to document in child-mcp-compatibility.md once the decision is in. Tests: tests/test_server_events.py.

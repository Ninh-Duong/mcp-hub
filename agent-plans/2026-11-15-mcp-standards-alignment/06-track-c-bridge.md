# Track C: stdio bridge

Status: ready
Branch: `arch/track-c` (from `arch/base`). Worktree: `../mcp-hub-track-c` (sibling of the Hub checkout).

Owns:
- `src/mcp_hub/bridge.py`
- `src/mcp_hub/bridge_proxy.py` (new)
- `tests/test_bridge.py`

Read first: all of `src/mcp_hub/bridge.py` (about 90 lines). Line references from the review: `PRESENCE_RETRY_SECONDS` and `BRIDGE_TIMEOUT` around lines 29-31; the instructions one-liner around lines 63-65; progress is not passed around line 52; the presence stream drops events around lines 86-88.

Do not edit `server.py` or `http.py` (Phase 2 owns the Hub side).

## T30 split without behaviour change [x]

Move the forwarding handlers (tools and prompts) into `src/mcp_hub/bridge_proxy.py`. `bridge.py` keeps startup and lifecycle only. Existing tests must pass unchanged.

## T31 instructions [x]

Replace the hard-coded one-line instructions with the instructions the Hub sent in its `initialize` result. Read the attribute from the SDK client (verify the name, for example on `initialize_result`). If the Hub sent none, keep the current one-liner.

Test: a fake upstream with instructions gives the same text downstream; with none, the one-liner is used.

## T32 progress [x]

When a downstream `tools/call` carries a `progressToken` in `_meta`, pass a `progress_callback` to the upstream `call_tool`. Each upstream progress is re-sent downstream as `notifications/progress` with the same token (verify the SDK helper on the request context). Forward the other `_meta` keys except the ones that belong to the connection (protocol version, client info, capabilities).

Test: progress values arrive in order, and the token is the downstream one.

## T33 list changes [x]

When the presence stream reports a tools list change, send `notifications/tools/list_changed` to the stdio client (verify the SDK method on the server session). If the stream raises `ListenNotSupportedError` (verify the name), log once and continue without relaying.

Test: one upstream event produces one downstream notification; the not-supported case does not crash.

## T34 timeouts and defaults

The bridge runs as a separate process that clients start, and it must not read the Hub config. So its tunables take their defaults from `mcp_hub.settings.BridgeSettings()`. Replace the module constants with:

```python
PRESENCE_RETRY_SECONDS = BridgeSettings().presence_retry_seconds
BRIDGE_TIMEOUT = BridgeSettings().connect_timeout_seconds
```

Keep those two names, because `hub_http_client` and tests import them. Record the deviation from the general "passed at start" rule in the progress log: the bridge has no start-time settings channel.

Test: the constants equal the `BridgeSettings()` defaults (5 and 60).

## Verify

```
cd "../mcp-hub-track-c"
uv run --no-sync python -m unittest tests.test_bridge -v
uv run --no-sync python -m ruff check .
uv run --no-sync python scripts/check_code_standards.py
uv run --no-sync python scripts/package_qc.py
```

## Stop and ask

- The SDK client does not expose the upstream instructions or progress hooks named above.
- Forwarding progress needs changes to `server.py` (then stop; Phase 2 does it).

## Progress log

- T30 done: forwarding handlers moved to `bridge_proxy.py`; `bridge.py` re-imports `create_bridge_server`; 7 bridge tests pass unchanged (plus one new module-location test).
- T31 done: `upstream_instructions(remote)` reads the SDK client's `instructions` (`getattr`, so fakes work); the one-liner is the fallback; 2 new tests.
- T32 done: bridge relays Hub progress with the stdio token and forwards other `_meta` keys (connection keys and the progress token stripped). The Hub does not emit child progress yet (`server.py` has no progress handling), so end-to-end progress needs W-T76. Request to track W: pass `progress_callback` and `_meta` to the child and relay its progress notifications to the client. The bridge side is ready.
- T33 BLOCKED (stop rule: a design decision the card does not state). Verified with the SDK: a 2025-11-25 stdio client receives `notifications/tools/list_changed` when the relay sends it through the session. A 2026-07-28 client receives nothing, because the SDK drops session-level notifications on modern connections (`NotifyOnlyOutbound`: delivered via `subscriptions/listen` at that era). The overview (Edge cases) says 2026 clients get changes through `subscriptions/listen`, so the bridge must also serve `subscriptions/listen` (SDK `ListenHandler` plus a bus that publishes `ToolsListChanged`). The card does not name the bus, and D6 names `HubEvents` as the one bus. Orchestrator decision needed: which bus the bridge uses, or whether the card should add the listen path. No T33 code is committed; the branch holds T30 to T32 only. T34 is not started.
- T33 DECISION (orchestrator): the bridge serves `subscriptions/listen` with the SDK `ListenHandler` on an `InMemorySubscriptionBus`, both from `mcp.server.subscriptions`. Not `events.py`, which is Track B's and is not on `arch/base`. Upstream `ToolsListChanged` is published on that bus (2026-era clients) and also sent as the session notification (2025-era clients). Upstream already debounces, so the bridge does not. The bus is process-local to the bridge; D6 (one bus) concerns the Hub process.
- T33 done: tests cover one 2025 session notification, one 2026 `subscriptions/listen` event (the prompts event is ignored), and the not-supported case, which logs once and stops presence.

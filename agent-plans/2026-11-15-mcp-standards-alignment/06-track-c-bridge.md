# Track C: stdio bridge

Status: ready
Branch: `arch/track-c` (from `arch/base`). Worktree: `../mcp-hub-track-c` (sibling of the Hub checkout).

Owns:
- `src/mcp_hub/bridge.py`
- `src/mcp_hub/bridge_proxy.py` (new)
- `tests/test_bridge.py`

Read first: all of `src/mcp_hub/bridge.py` (about 90 lines). Line references from the review: `PRESENCE_RETRY_SECONDS` and `BRIDGE_TIMEOUT` around lines 29-31; the instructions one-liner around lines 63-65; progress is not passed around line 52; the presence stream drops events around lines 86-88.

Do not edit `server.py` or `http.py` (Phase 2 owns the Hub side).

## T30 split without behaviour change

Move the forwarding handlers (tools and prompts) into `src/mcp_hub/bridge_proxy.py`. `bridge.py` keeps startup and lifecycle only. Existing tests must pass unchanged.

## T31 instructions

Replace the hard-coded one-line instructions with the instructions the Hub sent in its `initialize` result. Read the attribute from the SDK client (verify the name, for example on `initialize_result`). If the Hub sent none, keep the current one-liner.

Test: a fake upstream with instructions gives the same text downstream; with none, the one-liner is used.

## T32 progress

When a downstream `tools/call` carries a `progressToken` in `_meta`, pass a `progress_callback` to the upstream `call_tool`. Each upstream progress is re-sent downstream as `notifications/progress` with the same token (verify the SDK helper on the request context). Forward the other `_meta` keys except the ones that belong to the connection (protocol version, client info, capabilities).

Test: progress values arrive in order, and the token is the downstream one.

## T33 list changes

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

- (agent: add one line per task here)

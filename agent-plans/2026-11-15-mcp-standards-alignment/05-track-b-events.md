# Track B: events and child list changes

Status: ready
Branch: `arch/track-b` (from `arch/base`). Worktree: `../mcp-hub-track-b` (sibling of the Hub checkout).

Owns:
- `src/mcp_hub/events.py` (new)
- `src/mcp_hub/children.py`
- `tests/fake_stdio_child.py` (add a mode only)
- `tests/test_events.py` (new)
- `tests/test_children.py` (additions only)

Do not edit `server.py` or `activity_ui/` (Phase 2 wires them).

SDK facts already verified in the Hub's venv (`mcp` 2.2.0): `mcp.server.subscriptions` exports `InMemorySubscriptionBus`, `ListenHandler`, `SubscriptionBus`, `ToolsListChanged`. `mcp.Client.__init__` takes `message_handler`. Read the SDK source under `.venv/Lib/site-packages/mcp/` to confirm the publish method and the listen-handler constructor, and the notification type for a child's tools list change (`ToolListChangedNotification` is the likely name). If a name differs, use the real one and say so in the progress log.

## T20 `HubEvents` (done)

File: `src/mcp_hub/events.py`. Test: `tests/test_events.py`.

```python
class HubEvents:
    def __init__(self, debounce_seconds: float) -> None: ...
    bus: InMemorySubscriptionBus          # attribute
    listen_handler: ListenHandler         # attribute, handed to the server by Phase 2
    def prime(self, signature: str) -> None: ...        # set the baseline; never publishes
    async def tools_changed(self, signature: str) -> bool: ...  # True if a publish was scheduled
    def close(self) -> None: ...          # cancel any pending publish
```

Behaviour:
- `prime` records the signature without publishing (used at start, before any client exists).
- `tools_changed` with the same signature as the last one returns False.
- A different signature records it, cancels a pending publish, and schedules one `ToolsListChanged()` publish after `debounce_seconds`. A burst within the window produces one publish, carrying no payload beyond the notification.
- `close` cancels the pending task.

Tests (use a real short debounce such as 0.02 seconds and `asyncio.sleep`): prime then same signature publishes nothing; a burst of five different signatures inside the window publishes exactly once; a change after the window publishes again; `close` cancels a pending publish. Count publishes by subscribing to the bus with the SDK's subscription API (read the SDK to find it).

## T21 child list changes

File: `src/mcp_hub/children.py`. Test: `tests/test_children.py` (additions) and `tests/fake_stdio_child.py` (new mode).

- The supervisor gets an optional `on_tools_changed: Callable[[str], Awaitable[None]] | None = None` argument. It is called with the server id.
- The `Client(...)` built around line 91 receives `message_handler=`. When the child sends a tools list-changed notification, the handler relists the child's tools (reuse the existing listing code, including pagination) and awaits `on_tools_changed(server_id)`.
- The handler must not raise into the SDK: catch and log failures.
- Fake child: add a mode, selected by an environment variable (for example `FAKE_CHILD_TOOL_CHANGE=1`), in which the child sends the notification once after it answers its first `tools/list`, with one more tool the second time.

Tests: the callback runs once with the server id when the child announces a change; the relist sees the new tool; a callback that raises does not kill the supervisor (the next tools call still works).

## Verify

```
cd "../mcp-hub-track-b"
uv run --no-sync python -m unittest tests.test_events tests.test_children -v
uv run --no-sync python -m ruff check .
uv run --no-sync python scripts/check_code_standards.py
uv run --no-sync python scripts/package_qc.py
```

## Stop and ask

- The SDK does not expose the publish or listen APIs named here.
- The fake child change needs more than an environment flag.

## Progress log

- T20 done: `HubEvents` in `src/mcp_hub/events.py` with 5 tests in `tests/test_events.py`. SDK names match the card (mcp 2.2.0): `InMemorySubscriptionBus`, `ListenHandler`, `ToolsListChanged` from `mcp.server.subscriptions`; `bus.subscribe(listener)` is the test's counting API.
- (agent: add one line per task here)

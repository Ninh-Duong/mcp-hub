"""Shared test fakes (fakes). Test modules import them from here instead of from each other."""
from __future__ import annotations

from __future__ import annotations
from types import SimpleNamespace
from mcp.types import Tool


class FakeProtector:
    def protect(self, data: bytes) -> bytes:
        return b"enc:" + bytes(byte ^ 0xA5 for byte in data)

    def unprotect(self, data: bytes) -> bytes:
        if not data.startswith(b"enc:"):
            raise ValueError("not protected")
        return bytes(byte ^ 0xA5 for byte in data[4:])


class FakeRuntime:
    config = SimpleNamespace(detected=("sample-child",), servers={"sample-child": object()}, unavailable={})
    clients = {"sample-child": object()}

    def child_states(self):
        return {"sample-child": "connected"}

    def config_incomplete(self):
        return {"sample-child": ["openobserve.dev.token"]}

    def activity_snapshot(self):
        return {
            "children": {"sample-child": {"tool_count": 2, "active_calls": 0}},
            "events": [], "sequence": 0, "stream_id": "fake-stream",
        }


class FakeResponse:
    def __init__(self, lines: list[bytes]) -> None:
        self.lines = lines

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def __iter__(self):
        return iter(self.lines)


class FakeStop:
    """A stop event that records each backoff wait and stops after `waits` of them."""

    def __init__(self, waits: int) -> None:
        self.delays: list[float] = []
        self.waits = waits
        self.stopped = False

    def is_set(self) -> bool:
        return self.stopped

    def wait(self, delay: float) -> bool:
        self.delays.append(delay)
        self.stopped = len(self.delays) >= self.waits
        return self.stopped


def tool(name: str, read_only: bool) -> Tool:
    return Tool(name=name, input_schema={"type": "object"}, annotations={"readOnlyHint": read_only})


def plain_tool(name: str) -> Tool:
    return Tool(name=name, input_schema={"type": "object"}, annotations={"readOnlyHint": True})

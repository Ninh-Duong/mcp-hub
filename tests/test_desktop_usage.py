"""desktop/usage.py: the activity stream parser, the per-child usage board, and the stream worker (no Qt)."""
from __future__ import annotations

import json
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import URLError

from mcp_hub.desktop import status, theme, usage
from mcp_hub.desktop.usage import ChildUsage, UsageBoard

NOW = datetime(2026, 10, 31, 9, 30, tzinfo=timezone.utc)
TOKEN = "t" * 48


def event(state: str, call_id: int, client: str = "Codex", tool: str = "query_logs", **extra: object) -> dict:
    return {"server_id": "logs", "call_id": call_id, "state": state, "client": client, "tool": tool, **extra}


class ParseSseTest(unittest.TestCase):
    def test_events_comments_and_bad_data(self) -> None:
        lines = [
            "id: s:1\n", "event: snapshot\n", 'data: {"activity":\n', 'data: {"children": {}}}\n', "\n",
            ": keep-alive\n", "\n",
            "event: activity\n", "data: not json\n", "\n",
            "event: activity\n", "data: [1, 2]\n", "\n",
            "data:{\"state\": \"started\"}\r\n", "\r\n",
        ]
        self.assertEqual(list(usage.parse_sse(lines)), [
            ("snapshot", {"activity": {"children": {}}}),
            ("message", {"state": "started"}),
        ])


class UsageBoardTest(unittest.TestCase):
    def test_running_calls_then_recent_use_then_nothing(self) -> None:
        board = UsageBoard()
        self.assertIsNone(board.usage("logs", NOW))
        board.apply_event(event("started", 1), NOW)
        board.apply_event(event("started", 2, client="Antigravity"), NOW)
        board.apply_event(event("started", 3), NOW)
        self.assertEqual(board.usage("logs", NOW), ChildUsage("active", ("Codex ×2", "Antigravity"), "query_logs"))
        board.apply_event(event("started", 4, tool="search"), NOW)
        self.assertIsNone(board.usage("logs", NOW).tool)  # several tools: clients only
        for call_id in (1, 2, 3):
            board.apply_event(event("succeeded", call_id), NOW)
        board.apply_event(event("failed", 4, tool="search", at=NOW.isoformat()), NOW)
        self.assertEqual(board.usage("logs", NOW + timedelta(seconds=59)), ChildUsage("recent", ("Codex",), "search", 0))
        self.assertEqual(board.usage("logs", NOW + timedelta(seconds=61)).minutes_ago, 1)
        self.assertIsNone(board.usage("logs", NOW + timedelta(seconds=theme.RECENT_SECONDS)))

    def test_client_usage_maps_each_client_to_its_children(self) -> None:
        board = UsageBoard()
        board.apply_event({**event("started", 1), "server_id": "logs"}, NOW)
        board.apply_event({**event("started", 2), "server_id": "jira"}, NOW)
        board.apply_event({**event("started", 3), "server_id": "logs"}, NOW)
        board.apply_event({**event("succeeded", 4, client="Codex"), "server_id": "pr", "at": NOW.isoformat()}, NOW)
        board.apply_event({**event("succeeded", 5, client="Antigravity"), "server_id": "pr", "at": NOW.isoformat()}, NOW)
        board.apply_event({**event("succeeded", 6, client="Antigravity"), "server_id": "src", "at": NOW.isoformat()}, NOW)
        # Codex runs calls now, so its recent use does not count; pr's last call was Antigravity's.
        self.assertEqual(board.client_usage(NOW), {"Codex": ("active", ("logs", "jira")),
                                                   "Antigravity": ("recent", ("pr", "src"))})
        self.assertEqual(board.client_usage(NOW + timedelta(seconds=theme.RECENT_SECONDS)),
                         {"Codex": ("active", ("logs", "jira"))})

    def test_unknown_finish_bad_events_and_missing_times(self) -> None:
        board = UsageBoard()
        board.apply_event({"state": "started", "call_id": 1}, NOW)  # no server: ignored
        board.apply_event({"server_id": "logs", "call_id": 9, "state": "interrupted", "at": "garbage"}, NOW)
        self.assertEqual(board.usage("logs", NOW), ChildUsage("recent", ("unknown",), None, 0))
        board.apply_event(event("running?", 5), NOW)
        self.assertEqual(board.usage("logs", NOW).kind, "recent")

    def test_snapshot_replaces_the_board(self) -> None:
        board = UsageBoard()
        board.apply_event(event("started", 1), NOW)
        board.load_snapshot({"children": {
            "logs": {"active_call_details": [{"call_id": 7, "tool": "query_logs", "client": "Antigravity"}, "junk"]},
            "jira": {"last_client": "Codex", "last_tool": "read", "last_at": "2026-10-31T09:29:30"},
            "pr": {"last_client": None, "last_at": NOW.isoformat()},
            "bad": "junk",
        }})
        self.assertEqual(board.usage("logs", NOW), ChildUsage("active", ("Antigravity",), "query_logs"))
        self.assertEqual(board.usage("jira", NOW), ChildUsage("recent", ("Codex",), "read", 0))
        self.assertIsNone(board.usage("pr", NOW))
        board.load_snapshot({"children": "junk"})
        self.assertIsNone(board.usage("logs", NOW))


class FakeResponse:
    def __init__(self, lines: list[bytes]) -> None:
        self.lines = lines

    def __enter__(self) -> "FakeResponse":
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


class StreamTest(unittest.TestCase):
    def test_open_sends_the_bearer_to_the_activity_stream_and_hides_failures(self) -> None:
        requests = []

        def opener(request, timeout):
            requests.append((request.full_url, request.get_header("Authorization"), timeout))
            return "response"

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "service.json").write_text(json.dumps({"version": 1, "host": "127.0.0.1", "port": 8765, "token": TOKEN}),
                                               encoding="utf-8")
            self.assertEqual(usage.open_activity_stream(opener=opener, private_root=root), "response")

            def refused(_request, timeout):
                raise URLError("refused")

            with self.assertRaises(status.DesktopError) as caught:
                usage.open_activity_stream(opener=refused, private_root=root)
            self.assertNotIn(TOKEN, str(caught.exception))
            with self.assertRaises(status.DesktopError):
                usage.open_activity_stream(opener=opener, private_root=root / "missing")
        self.assertEqual(requests, [("http://127.0.0.1:8765/dashboard/api/activity/stream", f"Bearer {TOKEN}",
                                     usage.STREAM_READ_TIMEOUT_SECONDS)])

    def test_worker_forwards_messages_and_backs_off_between_failures(self) -> None:
        connects = iter([
            status.DesktopError("down"), OSError("reset"),
            FakeResponse([b"event: snapshot\n", b'data: {"activity": {"children": {}}}\n', b"\n",
                          b"event: activity\n", b'data: {"state": "started"}\n', b"\n",
                          b"event: snapshot\n", b'data: {"activity": 3}\n', b"\n"]),
            OSError("reset"), OSError("reset"), OSError("reset"), OSError("reset"), OSError("reset"), OSError("reset"),
        ])

        def connect():
            item = next(connects)
            if isinstance(item, Exception):
                raise item
            return item

        put: list[tuple] = []
        stop = FakeStop(waits=9)
        usage.stream_usage(put.append, stop, connect=connect)
        self.assertEqual(put, [("usage_snapshot", {"children": {}}), ("usage_event", {"state": "started"}),
                               ("usage_snapshot", {})])
        # 1 → 2, reset after a good connection, then doubling up to the cap.
        self.assertEqual(stop.delays, [1.0, 2.0, 1.0, 2.0, 4.0, 8.0, 16.0, 30.0, 30.0])

    def test_worker_stops_while_reading(self) -> None:
        stop = threading.Event()
        lines = [b"event: activity\n", b'data: {"state": "started"}\n', b"\n"]

        def connect():
            stop.set()
            return FakeResponse(lines)

        put: list[tuple] = []
        usage.stream_usage(put.append, stop, connect=connect)
        self.assertEqual(put, [])


if __name__ == "__main__":
    unittest.main()

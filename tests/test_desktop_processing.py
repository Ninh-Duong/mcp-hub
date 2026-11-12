"""Offline processing-stream contract and lifecycle tests."""
import json
import threading
import unittest
from http.client import HTTPException, IncompleteRead
from unittest.mock import patch
from urllib.error import HTTPError

from mcp_hub.desktop import processing
from mcp_hub.desktop.connection import Connection
from tests.test_desktop_usage import FakeResponse, FakeStop


def event(sequence=1, stream_id="hub-one", **values):
    return dict(stream_id=stream_id, sequence=sequence, at="2026-11-12T03:00:00+00:00",
                operation="plan", state="started", source="bitnet", **values)


def snapshot(sequence=1, stream_id="hub-one", events=None):
    return dict(stream_id=stream_id, sequence=sequence, events=events if events is not None else [event(sequence, stream_id)])


def lines(name, data):
    return [f"event: {name}\n", f"data: {json.dumps(data)}\n", "\n"]


class ProcessingTest(unittest.TestCase):
    def test_snapshot_event_resync_and_restart_keep_newest_three_without_duplicates(self):
        board = processing.ProcessingBoard()
        self.assertTrue(board.apply(processing.parse_message("snapshot", snapshot(2, events=[event(1), event(2)]))))
        self.assertFalse(board.apply(processing.parse_message("event", event(2))))
        board.apply(processing.parse_message("event", event(3)))
        board.apply(processing.parse_message("snapshot", snapshot(5, events=[event(3), event(4), event(5)])))
        self.assertEqual([item.sequence for item in board.events], [5, 4, 3])
        self.assertFalse(board.apply(processing.parse_message("snapshot", snapshot(2))))
        board.apply(processing.parse_message("snapshot", snapshot(1, "hub-two")))
        self.assertEqual([(item.stream_id, item.sequence) for item in board.events], [("hub-two", 1)])
        self.assertFalse(board.apply(processing.parse_message("event", event(9))))
        board.clear()
        self.assertEqual(board.events, ())

    def test_unknown_malformed_and_content_bearing_fields_are_not_retained(self):
        for data in [dict(event(), sequence=True), dict(event(), operation="unknown"),
                     dict(event(), state="unknown"), dict(event(), operation=[]),
                     dict(event(), state={}), dict(event(), source=[]), dict(event(), error_code=[]), dict(event(), source="unknown"),
                     dict(event(), at="invalid"), dict(event(), duration_ms=-1),
                     dict(event(), error_code="secret"), dict(event(), server_id=[]),
                     dict(event(), tool_name="https://private.example.com")]:
            self.assertIsNone(processing.parse_message("event", data), data)
        for name, data in [("keepalive", {}), ("other", event()), ("snapshot", {}),
                           ("snapshot", snapshot(1, events=[dict(event(), state=1)]))]:
            self.assertIsNone(processing.parse_message(name, data))
        message = processing.parse_message("event", dict(event(), prompt="do not store"))
        self.assertNotIn("do not store", repr(message))

    def test_worker_parses_sse_and_stops_without_forwarding_late_messages(self):
        output = []
        stop = FakeStop(1)
        data = lines("snapshot", snapshot()) + [": keepalive\n", "\n"] + lines("event", event(2))
        processing.stream_processing(output.append, stop, connect=lambda: FakeResponse([s.encode() for s in data]))
        self.assertEqual([message.kind for _, message in output], ["snapshot", "event"])
        stop = threading.Event()
        def connect():
            stop.set()
            return FakeResponse([s.encode() for s in data])
        processing.stream_processing(output.append, stop, connect=connect)
        self.assertEqual(len(output), 2)

    def test_worker_failure_backoff_is_bounded_and_absent_old_route_is_quiet(self):
        stop = FakeStop(8)
        def failed():
            raise OSError("private failure")
        output = []
        processing.stream_processing(output.append, stop, connect=failed)
        self.assertEqual(stop.delays, [1, 2, 4, 8, 16, 30, 30, 30])
        self.assertEqual(output, [])
        for code in [401, 404]:
            stop = FakeStop(2)
            def old_hub():
                raise HTTPError("http://127.0.0.1", code, "absent", {}, None)
            processing.stream_processing(output.append, stop, connect=old_hub)
            self.assertEqual(stop.delays, [1, 2])
        self.assertEqual(output, [("processing_unavailable", None), ("processing_unavailable", None)])

    def test_truncated_transport_discards_partial_event_and_reconnects(self):
        for failure in (IncompleteRead(b"partial"), HTTPException("interrupted response")):
            with self.subTest(transport=type(failure).__name__):
                class TruncatedResponse(FakeResponse):
                    def __iter__(self):
                        yield b"event: event\n"
                        yield b'data: {"stream_id": "partial"'
                        raise failure

                responses = iter([
                    TruncatedResponse([]),
                    FakeResponse([line.encode() for line in lines("snapshot", snapshot(3))]),
                ])
                output, stop = [], FakeStop(2)
                processing.stream_processing(output.append, stop, connect=lambda: next(responses))
                self.assertEqual(stop.delays, [1, 1])
                self.assertEqual(len(output), 1)
                self.assertEqual(output[0][0], "processing")
                self.assertEqual(output[0][1].kind, "snapshot")
                self.assertEqual(output[0][1].sequence, 3)

    def test_opener_uses_fixed_loopback_endpoint_and_admin_bearer(self):
        requests = []
        def opener(request, timeout):
            requests.append((request.full_url, request.get_header("Authorization"), timeout))
            return "response"
        with patch.object(processing, "load_connection", return_value=Connection("http://127.0.0.1:8765", "x" * 32)):
            self.assertEqual(processing.open_processing_stream(opener=opener), "response")
        self.assertEqual(requests, [("http://127.0.0.1:8765/dashboard/api/bitnet/processing/stream", "Bearer " + "x" * 32, 40)])

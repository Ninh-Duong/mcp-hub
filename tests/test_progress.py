"""progress: shared _meta forwarding and progress relay (moved from server.py and bridge_proxy.py)."""
from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace

from mcp.types import CLIENT_CAPABILITIES_META_KEY, CLIENT_INFO_META_KEY, PROTOCOL_VERSION_META_KEY

from mcp_hub import progress


class _Session:
    def __init__(self) -> None:
        self.sent: list[tuple] = []

    async def send_progress_notification(self, token, progress_value, total, message, related_request_id=None) -> None:
        self.sent.append((token, progress_value, total, message, related_request_id))


class ProgressTest(unittest.TestCase):
    def test_the_connection_keys_are_the_sdk_constants(self) -> None:
        self.assertEqual(
            progress.CONNECTION_META_KEYS,
            frozenset({PROTOCOL_VERSION_META_KEY, CLIENT_INFO_META_KEY, CLIENT_CAPABILITIES_META_KEY}),
        )

    def test_forwarded_meta_drops_connection_keys_and_the_progress_token(self) -> None:
        meta = {"progress_token": 9, CLIENT_INFO_META_KEY: "x", PROTOCOL_VERSION_META_KEY: "y", "trace": "keep"}
        self.assertEqual(progress.forwarded_meta(meta), {"trace": "keep"})

    def test_forwarded_meta_of_nothing_is_empty(self) -> None:
        self.assertEqual(progress.forwarded_meta(None), {})

    def test_progress_token_is_none_without_one(self) -> None:
        self.assertIsNone(progress.progress_token({"trace": "x"}))
        self.assertIsNone(progress.progress_token(None))
        self.assertEqual(progress.progress_token({"progress_token": "t1"}), "t1")

    def test_the_sender_reports_under_the_clients_token_on_the_request_stream(self) -> None:
        session = _Session()
        ctx = SimpleNamespace(session=session, request_id=7)
        send = progress.progress_sender(ctx, "client-token")
        asyncio.run(send(2.0, 5.0, "half way"))
        self.assertEqual(session.sent, [("client-token", 2.0, 5.0, "half way", 7)])


if __name__ == "__main__":
    unittest.main()

"""`hub__get_full_result` answers bad input with an error result and never raises (FR-003)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mcp_hub.audit import AuditLog
from mcp_hub.config import HubConfig
from mcp_hub.full_results import FullResultStore
from mcp_hub.server import HubRuntime
from mcp_hub.settings import Settings


class FullResultArgumentsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.audit = AuditLog(root / "audit.jsonl")
        self.addCleanup(self.audit.close)
        self.runtime = HubRuntime(HubConfig(root / "hub.json", {}, ()), self.audit, Settings())
        self.runtime.full_results = FullResultStore(max_entries=10, ttl_seconds=60.0)
        self.rid = self.runtime.full_results.put("alpha\nbeta\ngamma\n") or ""

    def call(self, **arguments: object):
        return self.runtime._get_full_result({"result_id": self.rid, **arguments}, "req-1")

    def test_limit_as_a_list_is_an_error_result(self) -> None:
        res = self.call(limit=["5"])
        self.assertTrue(res.is_error)
        self.assertIn("limit must be a whole number", res.content[0].text)

    def test_offset_as_text_is_an_error_result(self) -> None:
        res = self.call(offset="abc")
        self.assertTrue(res.is_error)
        self.assertIn("offset must be a whole number", res.content[0].text)

    def test_zero_limit_is_an_error_result(self) -> None:
        res = self.call(limit=0)
        self.assertTrue(res.is_error)
        self.assertIn("limit must be 1 or more", res.content[0].text)

    def test_boolean_limit_is_an_error_result(self) -> None:
        res = self.call(limit=True)
        self.assertTrue(res.is_error)

    def test_invalid_regex_is_an_error_result(self) -> None:
        res = self.call(regex="(unclosed")
        self.assertTrue(res.is_error)
        self.assertIn("Invalid regex", res.content[0].text)

    def test_valid_paging_arguments_read_the_result(self) -> None:
        res = self.call(offset=0, limit=5)
        self.assertFalse(res.is_error)
        self.assertIn("alpha", res.content[0].text)

    def test_string_digits_are_accepted_as_a_number(self) -> None:
        res = self.call(limit="5")
        self.assertFalse(res.is_error)


if __name__ == "__main__":
    unittest.main()

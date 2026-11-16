"""jsonio: the shared JSON reader and atomic writer (research.md R3)."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mcp_hub.jsonio import read_json, write_json_atomic


class JsonioTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_reads_a_utf8_file_with_a_byte_order_mark(self) -> None:
        path = self.root / "bom.json"
        path.write_bytes(b"\xef\xbb\xbf" + json.dumps({"name": "hub"}).encode("utf-8"))
        self.assertEqual(read_json(path), {"name": "hub"})

    def test_invalid_json_raises_decode_error(self) -> None:
        path = self.root / "broken.json"
        path.write_text("{not json", encoding="utf-8")
        with self.assertRaises(json.JSONDecodeError):
            read_json(path)

    def test_write_replaces_the_file_and_leaves_no_temporary_file(self) -> None:
        path = self.root / "state.json"
        write_json_atomic(path, {"b": 1, "a": 2}, indent=2, sort_keys=True, trailing_newline=True)
        self.assertEqual(path.read_text(encoding="utf-8"), '{\n  "a": 2,\n  "b": 1\n}\n')
        self.assertEqual([entry.name for entry in self.root.iterdir()], ["state.json"])

    def test_separators_are_honored(self) -> None:
        path = self.root / "compact.json"
        write_json_atomic(path, {"a": [1, 2]}, separators=(",", ":"))
        self.assertEqual(path.read_text(encoding="utf-8"), '{"a":[1,2]}')

    def test_a_value_that_cannot_be_encoded_leaves_the_old_file_unchanged(self) -> None:
        path = self.root / "keep.json"
        path.write_text('{"kept": true}', encoding="utf-8")
        with self.assertRaises(TypeError):
            write_json_atomic(path, {"bad": object()})
        self.assertEqual(path.read_text(encoding="utf-8"), '{"kept": true}')
        self.assertEqual([entry.name for entry in self.root.iterdir()], ["keep.json"])

    def test_a_failure_during_replace_removes_the_temporary_file(self) -> None:
        path = self.root / "fail.json"
        with patch("mcp_hub.jsonio.os.replace", side_effect=OSError("locked")):
            with self.assertRaises(OSError):
                write_json_atomic(path, {"a": 1})
        self.assertFalse(path.exists())
        self.assertEqual(list(self.root.iterdir()), [])

    def test_missing_parent_folder_is_created(self) -> None:
        path = self.root / "nested" / "deep" / "file.json"
        write_json_atomic(path, {"ok": True})
        self.assertEqual(read_json(path), {"ok": True})

    @unittest.skipIf(sys.platform == "win32", "POSIX permission bits; Windows ignores chmod for this purpose")
    def test_mode_is_applied_to_the_written_file(self) -> None:
        path = self.root / "secret.json"
        write_json_atomic(path, {"token": "x"}, mode=0o600)
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)

    def test_mode_failure_is_ignored_and_the_write_still_happens(self) -> None:
        path = self.root / "windows.json"
        with patch("mcp_hub.jsonio.os.chmod", side_effect=OSError("not supported")):
            write_json_atomic(path, {"ok": 1}, mode=0o600)
        self.assertEqual(read_json(path), {"ok": 1})


if __name__ == "__main__":
    unittest.main()

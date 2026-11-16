"""clients: normalized client names for logs and routing (moved from server.py)."""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from mcp_hub.clients import normalize_client_name, request_client_name


class NormalizeClientNameTest(unittest.TestCase):
    def test_non_string_values_are_unknown(self) -> None:
        for value in (None, 7, ["x"]):
            with self.subTest(value=value):
                self.assertEqual(normalize_client_name(value), "unknown")

    def test_disallowed_characters_are_removed_and_spaces_collapse(self) -> None:
        # Removed, not replaced: the tab and the slash and parentheses vanish, so "Code\t(v2)" becomes "Codev2".
        self.assertEqual(normalize_client_name("  Claude/Code\t(v2)  "), "ClaudeCodev2")
        self.assertEqual(normalize_client_name("Claude   Code  "), "Claude Code")

    def test_empty_after_cleaning_is_unknown(self) -> None:
        self.assertEqual(normalize_client_name("@@@"), "unknown")

    def test_long_names_are_cut_to_48_characters(self) -> None:
        self.assertEqual(len(normalize_client_name("a" * 100)), 48)


class RequestClientNameTest(unittest.TestCase):
    def test_reads_the_client_info_name_from_the_session(self) -> None:
        info = SimpleNamespace(name="Cursor IDE")
        ctx = SimpleNamespace(session=SimpleNamespace(client_params=SimpleNamespace(client_info=info)))
        self.assertEqual(request_client_name(ctx), "Cursor IDE")

    def test_without_client_info_it_is_unknown(self) -> None:
        self.assertEqual(request_client_name(SimpleNamespace(session=None)), "unknown")
        ctx = SimpleNamespace(session=SimpleNamespace(client_params=SimpleNamespace(client_info=None)))
        self.assertEqual(request_client_name(ctx), "unknown")


if __name__ == "__main__":
    unittest.main()

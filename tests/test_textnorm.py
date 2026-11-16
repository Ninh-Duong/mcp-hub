"""textnorm.fold replaces the two legacy copies; the legacy bodies are kept here as the reference (research.md R2)."""
from __future__ import annotations

import unicodedata
import unittest

from mcp_hub.textnorm import fold


def _legacy_bitnet_fold(text: str) -> str:
    text = text.replace("\u0111", "d").replace("\u0110", "d")
    return "".join(c for c in unicodedata.normalize("NFD", text) if not unicodedata.combining(c)).casefold()


def _legacy_plan_merge_fold(text: str) -> str:
    text = text.replace("\u0111", "d").replace("\u0110", "D")
    return "".join(c for c in unicodedata.normalize("NFD", text) if not unicodedata.combining(c)).casefold()


# Written with escapes so this file stays ASCII (AGENTS.md: English only).
SAMPLES = ["", "\u0110", "\u0111", "H\u00f4m nay", "\u0110\u1ea1i \u0111\u1ea7u", "Plain ASCII Text", "stra\u00dfe"]


class FoldTest(unittest.TestCase):
    def test_strips_diacritics_and_casefolds(self) -> None:
        self.assertEqual(fold("H\u00f4m nay"), "hom nay")
        self.assertEqual(fold("\u0110\u0111"), "dd")

    def test_empty_text_stays_empty(self) -> None:
        self.assertEqual(fold(""), "")

    def test_matches_both_legacy_copies_on_every_sample(self) -> None:
        for sample in SAMPLES:
            with self.subTest(sample=sample):
                self.assertEqual(fold(sample), _legacy_bitnet_fold(sample))
                self.assertEqual(fold(sample), _legacy_plan_merge_fold(sample))


if __name__ == "__main__":
    unittest.main()

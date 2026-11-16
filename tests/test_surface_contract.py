"""The Hub's external surface matches tests/contract_baseline/ (FR-010, SC-006).

A refactor phase must leave these files unchanged. An intended change regenerates the baseline with
`python -m tests.surface_capture` in the same commit, and the commit message explains it.
"""
from __future__ import annotations

import json
import unittest

from tests.surface_capture import BASELINE_DIR, capture_surface


def _normalized(data: object) -> object:
    """The JSON form of `data`, so tuples and sorted keys compare the same way the files store them."""
    return json.loads(json.dumps(data, sort_keys=True))


class SurfaceContractTest(unittest.TestCase):
    def test_every_surface_has_a_baseline_file(self) -> None:
        for name in capture_surface():
            with self.subTest(surface=name):
                self.assertTrue((BASELINE_DIR / name).is_file(), f"missing baseline {name}")

    def test_surface_matches_the_baseline(self) -> None:
        for name, data in capture_surface().items():
            with self.subTest(surface=name):
                expected = json.loads((BASELINE_DIR / name).read_text(encoding="utf-8"))
                self.assertEqual(_normalized(data), expected)


if __name__ == "__main__":
    unittest.main()

"""Test hygiene (US6): test modules do not import each other, and shared fakes live in tests/helpers.

A test module that imports another test module couples their setup and makes a failure in one hide the other.
Shared fakes belong in tests/helpers/, which test modules import from.
"""
from __future__ import annotations

import ast
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent


def _test_module_imports(path: Path) -> list[str]:
    """Modules under tests.test_* that the file imports, with the import line number."""
    found: list[str] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("tests.test_"):
            found.append(f"{path.name}:{node.lineno} from {node.module}")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("tests.test_"):
                    found.append(f"{path.name}:{node.lineno} import {alias.name}")
    return found


class TestHygieneTest(unittest.TestCase):
    def test_no_test_module_imports_another_test_module(self) -> None:
        offenders: list[str] = []
        for path in sorted(TESTS.glob("test_*.py")):
            offenders.extend(_test_module_imports(path))
        self.assertEqual(offenders, [], "test modules must import shared fakes from tests/helpers")

    def test_helpers_do_not_import_test_modules(self) -> None:
        offenders: list[str] = []
        for path in sorted((TESTS / "helpers").glob("*.py")):
            offenders.extend(_test_module_imports(path))
        self.assertEqual(offenders, [], "tests/helpers must not depend on test modules")


if __name__ == "__main__":
    unittest.main()

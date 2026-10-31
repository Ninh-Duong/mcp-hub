from __future__ import annotations

import unittest

from scripts import check_code_standards as standards


class LanguageCheckTest(unittest.TestCase):
    def test_flags_vietnamese_but_not_western_accents(self) -> None:
        lines = [
            "plain English",
            "naive café résumé",
            "Xin ch\u00e0o \u0111\u00e2y l\u00e0 Hub",
            "Ho\u1ea1t \u0111\u1ed9ng",
        ]
        self.assertEqual(standards.non_english_lines(lines), [3, 4])

    def test_only_vietnamese_ui_translations_may_hold_vietnamese(self) -> None:
        """Code, comments, logs, errors, installer text, and docs are English; only the vi locale data is not."""
        self.assertTrue(standards.LANGUAGE_ALLOWLIST)
        for path in standards.LANGUAGE_ALLOWLIST:
            with self.subTest(path=path):
                self.assertTrue(path.endswith("/locales/vi.json"))


class FunctionLengthCheckTest(unittest.TestCase):
    def test_python_functions_over_the_limit_are_reported(self) -> None:
        body = "\n".join(f"    x{index} = {index}" for index in range(5))
        source = f"def short():\n    return 1\n\n\ndef long():\n{body}\n"
        self.assertEqual(standards.long_python_functions(source, limit=5), [(5, "long", 6)])

    def test_js_top_level_functions_are_measured_to_their_closing_brace(self) -> None:
        lines = [
            "export async function load(path) {",
            "  const a = 1;",
            "  if (a) {",
            "    return a;",
            "  }",
            "}",
            "function tiny() { return 1; }",
            "function ok() {",
            "  return 2;",
            "}",
        ]
        self.assertEqual(standards.long_js_functions(lines, limit=4), [(1, "load", 6)])
        self.assertEqual(standards.long_js_functions(lines, limit=6), [])


class TestCoverageCheckTest(unittest.TestCase):
    def test_module_names_skip_package_markers(self) -> None:
        self.assertEqual(standards.module_name("src/mcp_hub/jobs/store.py"), "mcp_hub.jobs.store")
        self.assertIsNone(standards.module_name("src/mcp_hub/__init__.py"))
        self.assertIsNone(standards.module_name("src/mcp_hub/__main__.py"))

    def test_imports_cover_modules_and_submodules_named_in_from_imports(self) -> None:
        source = "import mcp_hub.audit\nfrom mcp_hub.jobs import store\nfrom mcp_hub.config import HubConfig\n"
        imported = standards.imported_modules(source)
        self.assertIn("mcp_hub.audit", imported)
        self.assertIn("mcp_hub.jobs.store", imported)
        self.assertIn("mcp_hub.config", imported)


if __name__ == "__main__":
    unittest.main()

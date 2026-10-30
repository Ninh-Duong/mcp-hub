from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


DASHBOARD = Path(__file__).resolve().parents[1] / "src" / "mcp_hub" / "dashboard"
LOCALES = DASHBOARD / "locales"

# Letters that only appear in Vietnamese text (base letters with Vietnamese-specific marks,
# the Latin Extended Additional block used for tone marks, and the combining tone marks).
VIETNAMESE_CHARACTERS = re.compile(
    "[\u0102\u0103\u00c2\u00e2\u0110\u0111\u00ca\u00ea\u00d4\u00f4\u01a0\u01a1\u01af\u01b0"
    "\u00c0-\u00c3\u00e0-\u00e3\u00c8-\u00ca\u00e8-\u00ea\u00cc\u00cd\u00ec\u00ed\u0128\u0129"
    "\u00d2-\u00d5\u00f2-\u00f5\u00d9\u00da\u00f9\u00fa\u0168\u0169\u00dd\u00fd"
    "\u1ea0-\u1ef9\u0300\u0301\u0303\u0309\u0323]"
)
T_CALL_KEY = re.compile(r"""\bt\(\s*["']([^"']+)["']""")
DATA_I18N_KEY = re.compile(r"""\bdata-i18n=["']([^"']+)["']""")
DATA_I18N_ATTR = re.compile(r"""\bdata-i18n-attr=["']([^"']+)["']""")
STRING_LITERAL = re.compile(r"""["']([a-zA-Z]+(?:\.[a-zA-Z]+)+)["']""")


def _load_locale(lang: str) -> dict[str, str]:
    return json.loads((LOCALES / f"{lang}.json").read_text(encoding="utf-8"))


def _scripts() -> dict[str, str]:
    return {path.name: path.read_text(encoding="utf-8") for path in sorted(DASHBOARD.glob("*.js"))}


class DashboardI18nTest(unittest.TestCase):
    def setUp(self) -> None:
        self.en = _load_locale("en")
        self.vi = _load_locale("vi")
        self.html = (DASHBOARD / "index.html").read_text(encoding="utf-8")
        self.scripts = _scripts()

    def test_locales_share_one_key_set_of_non_empty_strings(self) -> None:
        self.assertEqual(set(self.en), set(self.vi))
        for locale in (self.en, self.vi):
            for key, value in locale.items():
                self.assertIsInstance(value, str, key)
                self.assertTrue(value.strip(), key)

    def test_locales_use_the_same_placeholders(self) -> None:
        placeholder = re.compile(r"\{(\w+)\}")
        for key, text in self.en.items():
            self.assertEqual(set(placeholder.findall(text)), set(placeholder.findall(self.vi[key])), key)

    def test_every_static_html_key_exists(self) -> None:
        keys = set(DATA_I18N_KEY.findall(self.html))
        for spec in DATA_I18N_ATTR.findall(self.html):
            keys.update(pair.split(":", 1)[1].strip() for pair in spec.split(";") if ":" in pair)
        self.assertTrue(keys)
        self.assertEqual(keys - set(self.en), set())

    def test_every_literal_t_key_in_scripts_exists(self) -> None:
        keys = {key for source in self.scripts.values() for key in T_CALL_KEY.findall(source)}
        self.assertTrue(keys)
        self.assertEqual(keys - set(self.en), set())

    def test_every_key_like_literal_in_scripts_exists(self) -> None:
        # Catches keys passed through helpers such as translate(node, "live.live").
        namespaces = {key.split(".", 1)[0] for key in self.en}
        literals = {
            literal for source in self.scripts.values() for literal in STRING_LITERAL.findall(source)
            if literal.split(".", 1)[0] in namespaces
        }
        self.assertEqual(literals - set(self.en), set())

    def test_index_html_defaults_to_english(self) -> None:
        self.assertIn('<html lang="en">', self.html)
        self.assertIn('id="language"', self.html)
        self.assertIn('<script type="module" src="/dashboard/app.js">', self.html)

    def test_vietnamese_text_lives_only_in_vi_locale(self) -> None:
        sources = {"index.html": self.html, "locales/en.json": (LOCALES / "en.json").read_text(encoding="utf-8")}
        sources.update(self.scripts)
        sources["style.css"] = (DASHBOARD / "style.css").read_text(encoding="utf-8")
        sources["api.py"] = (DASHBOARD / "api.py").read_text(encoding="utf-8")
        for name, text in sources.items():
            self.assertIsNone(VIETNAMESE_CHARACTERS.search(text), name)
        self.assertIsNotNone(VIETNAMESE_CHARACTERS.search(" ".join(self.vi.values())))


if __name__ == "__main__":
    unittest.main()

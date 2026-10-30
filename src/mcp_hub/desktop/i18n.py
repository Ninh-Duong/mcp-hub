"""Desktop app text, read from the dashboard locale files so no translated text lives in code."""
from __future__ import annotations

import ctypes
import json
import re
from collections.abc import Callable
from pathlib import Path

LOCALES_DIR = Path(__file__).resolve().parents[1] / "dashboard" / "locales"
LANGUAGES = ("en", "vi")
_VIETNAMESE_PRIMARY_LANGUAGE = 0x2A
_PLACEHOLDER = re.compile(r"\{(\w+)\}")
# Child states reported by the Hub -> the dashboard's existing connection labels.
CONNECTION_KEYS = {
    "connected": "connection.connected",
    "connecting": "connection.connecting",
    "reconnecting": "connection.reconnecting",
    "disabled": "connection.disabled",
    "credentials required": "connection.credentialsRequired",
    "unavailable": "connection.unavailable",
}
HUB_KEYS = {
    "running": "desktop.hub.running",
    "starting": "desktop.hub.starting",
    "stopped": "desktop.hub.stopped",
    "not_installed": "desktop.hub.notInstalled",
    "unauthorized": "desktop.hub.unauthorized",
}


def load_strings(language: str) -> dict[str, str]:
    return json.loads((LOCALES_DIR / f"{language}.json").read_text(encoding="utf-8"))


def system_language(ui_language: Callable[[], int] | None = None) -> str:
    """`vi` when the Windows display language is Vietnamese, else `en` (also off Windows)."""
    if ui_language is None:
        try:
            ui_language = ctypes.windll.kernel32.GetUserDefaultUILanguage
        except (AttributeError, OSError):
            return "en"
    return "vi" if ui_language() & 0x3FF == _VIETNAMESE_PRIMARY_LANGUAGE else "en"


class Translator:
    def __init__(self, language: str = "en") -> None:
        self._english = load_strings("en")
        self.set_language(language)

    def set_language(self, language: str) -> None:
        self.language = language if language in LANGUAGES else "en"
        self._strings = self._english if self.language == "en" else load_strings(self.language)

    def other_language(self) -> str:
        return LANGUAGES[(LANGUAGES.index(self.language) + 1) % len(LANGUAGES)]

    def __call__(self, key: str, **values: object) -> str:
        text = self._strings.get(key) or self._english.get(key) or key
        return _PLACEHOLDER.sub(lambda match: str(values.get(match.group(1), match.group(0))), text)

    def state_label(self, state: str) -> str:
        key = CONNECTION_KEYS.get(state)
        return self(key) if key else (state or self("connection.unknown"))

    def hub_label(self, hub: str) -> str:
        return self(HUB_KEYS[hub])

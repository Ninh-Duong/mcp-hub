"""The desktop app's remembered choices in `config/private/desktop.json`: look, language, and the mini bar. No secrets."""
from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path

from ..config import PRIVATE_ROOT
from .i18n import LANGUAGES
from .theme import CARD_OPACITY_RANGE, GLASS_STYLES, THEMES, TINT_RANGE

PREFS_PATH = PRIVATE_ROOT / "desktop.json"


def _in_range(bounds: tuple[int, int]) -> Callable[[object], bool]:
    return lambda value: isinstance(value, int) and not isinstance(value, bool) and bounds[0] <= value <= bounds[1]


# Every key the file may hold, with the rule its value must meet; anything else is dropped.
_RULES: dict[str, Callable[[object], bool]] = {
    "theme": lambda value: value in THEMES,
    "language": lambda value: value in LANGUAGES,
    "glass": lambda value: isinstance(value, bool),
    "card_opacity": _in_range(CARD_OPACITY_RANGE),
    "tint": _in_range(TINT_RANGE),
    "glass_style": lambda value: value in GLASS_STYLES,
    "compact": lambda value: isinstance(value, bool),
    "compact_pos": lambda value: isinstance(value, list) and len(value) == 2
    and all(isinstance(item, int) and not isinstance(item, bool) for item in value),
}


def valid_prefs(value: object) -> dict[str, object]:
    """Known keys with valid values only."""
    if not isinstance(value, dict):
        return {}
    return {key: value[key] for key, rule in _RULES.items() if key in value and rule(value[key])}


def load_prefs(path: Path | None) -> dict[str, object]:
    """The saved choices; a missing or broken file means none."""
    if path is None:
        return {}
    try:
        return valid_prefs(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return {}


def save_prefs(path: Path | None, prefs: dict[str, object]) -> bool:
    """Write atomically; False when the file cannot be written (the choice then lasts for this session)."""
    if path is None:
        return False
    temp = path.with_name(path.name + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp.write_text(json.dumps(valid_prefs(prefs), indent=2) + "\n", encoding="utf-8")
        os.replace(temp, path)
    except OSError:
        temp.unlink(missing_ok=True)
        return False
    return True

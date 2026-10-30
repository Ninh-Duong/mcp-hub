"""The desktop app's remembered choices (theme and language) in `config/private/desktop.json`. No secrets."""
from __future__ import annotations

import json
import os
from pathlib import Path

from ..config import PRIVATE_ROOT
from .i18n import LANGUAGES
from .theme import THEMES

PREFS_PATH = PRIVATE_ROOT / "desktop.json"
_ALLOWED = {"theme": THEMES, "language": LANGUAGES}


def load_prefs(path: Path | None) -> dict[str, str]:
    """Known keys with allowed values only; a missing or broken file means no saved choice."""
    if path is None:
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(value, dict):
        return {}
    return {key: value[key] for key, allowed in _ALLOWED.items() if value.get(key) in allowed}


def save_prefs(path: Path | None, prefs: dict[str, str]) -> bool:
    """Write atomically; False when the file cannot be written (the choice then lasts for this session)."""
    if path is None:
        return False
    data = {key: prefs[key] for key, allowed in _ALLOWED.items() if prefs.get(key) in allowed}
    temp = path.with_name(path.name + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        os.replace(temp, path)
    except OSError:
        temp.unlink(missing_ok=True)
        return False
    return True
